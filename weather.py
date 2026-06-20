"""Weather driver for the heat pump simulator.

Two data sources (selected via ``[weather]`` in ``config/config.toml``):

  - **Calendar year** (``year = 2023``): hourly 2 m air temperature from
    Open-Meteo ERA5 archive for the lat/lon of the active house config
    (``[location]`` in ``house_config_*.toml``, overridden via ``HOUSE_CONFIG``).
    Cached under ``source_data/weather_{lat}_{lon}_{year}.csv`` after the first fetch.
  - **PVGIS TMY** (``year = 0``): stitched Typical Meteorological Year from
    ``source_data/tmy_48.351_10.164_2005_2023.csv``.

Both expose:

  - full_year()       : the full hourly outdoor temperature series
  - worst_case_year() : synthetic year stitched from the coldest calendar
                        month in each year of a historical range (default
                        2005–2023); scored by heating-degree-hours, longest
                        cold spell, then minimum temperature
"""

from __future__ import annotations

import csv
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

SOURCE_DIR = Path(__file__).with_name("source_data")
DEFAULT_TMY = SOURCE_DIR / "tmy_48.351_10.164_2005_2023.csv"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Add running hour/day indices used by the simulation."""
    df = df.reset_index(drop=True)
    df["hour"] = df.index
    df["day"] = df["hour"] // 24
    df["month"] = df["time"].dt.month
    return df


def load_tmy_month_years(path: Path = DEFAULT_TMY) -> dict[int, int]:
    """Read the PVGIS month→source-year table from a TMY CSV header."""
    mapping: dict[int, int] = {}
    for line in Path(path).read_text().splitlines():
        if not line or line.startswith("Latitude") or line.startswith("month,year"):
            continue
        parts = line.split(",")
        if len(parts) == 2 and parts[0].strip().isdigit():
            mapping[int(parts[0])] = int(parts[1])
        if line.startswith("time(UTC)"):
            break
    return mapping


def load_tmy(path: Path = DEFAULT_TMY) -> pd.DataFrame:
    """Parse a PVGIS TMY CSV into a tidy hourly DataFrame."""
    lines = Path(path).read_text().splitlines()
    month_years = load_tmy_month_years(path)
    start = next(i for i, ln in enumerate(lines) if ln.startswith("time(UTC)"))
    end = start + 1
    while end < len(lines) and lines[end].strip() and "," in lines[end]:
        end += 1
    block = "\n".join(lines[start:end])
    raw = pd.read_csv(io.StringIO(block))

    df = pd.DataFrame()
    df["time"] = pd.to_datetime(raw["time(UTC)"], format="%Y%m%d:%H%M")
    df["t_out"] = raw["T2m"].astype(float)
    df = _normalize(df)
    if month_years:
        df["source_year"] = df["month"].map(month_years).astype(int)
    return df


def weather_cache_path(latitude: float, longitude: float, year: int) -> Path:
    return SOURCE_DIR / f"weather_{latitude:.3f}_{longitude:.3f}_{year}.csv"


def fetch_calendar_year(latitude: float, longitude: float, year: int,
                        *, retries: int = 3) -> pd.DataFrame:
    """Download hourly 2 m temperature for one calendar year (UTC)."""
    params = urllib.parse.urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "hourly": "temperature_2m",
        "timezone": "UTC",
    })
    url = f"{OPEN_METEO_ARCHIVE}?{params}"
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                payload = json.load(resp)
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    else:
        cache = weather_cache_path(latitude, longitude, year)
        raise RuntimeError(
            f"Could not download weather for {year} ({latitude}, {longitude}): "
            f"{last_exc}\n"
            f"If you are offline, place a cached CSV at {cache}"
        ) from last_exc

    times = payload["hourly"]["time"]
    temps = payload["hourly"]["temperature_2m"]
    if len(times) != len(temps):
        raise RuntimeError("Open-Meteo response length mismatch")
    expected = 8784 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 8760
    if len(times) not in (8760, 8784):
        raise RuntimeError(f"Expected {expected} hourly values for {year}, got {len(times)}")

    df = pd.DataFrame({
        "time": pd.to_datetime(times, utc=True),
        "t_out": pd.array(temps, dtype=float),
    })
    return _normalize(df)


def save_weather_cache(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time", "t_out"])
        for row in df.itertuples(index=False):
            writer.writerow([row.time.isoformat(), f"{row.t_out:.2f}"])


def load_weather_cache(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    df = pd.DataFrame()
    df["time"] = pd.to_datetime(raw["time"], utc=True)
    df["t_out"] = raw["t_out"].astype(float)
    return _normalize(df)


def fetch_weather_years(latitude: float, longitude: float,
                        years: list[int]) -> tuple[list[int], list[int]]:
    """Download/cache a list of calendar years; continue past individual failures."""
    ok: list[int] = []
    failed: list[int] = []
    for y in sorted(set(years)):
        cache = weather_cache_path(latitude, longitude, y)
        if cache.exists():
            ok.append(y)
            print(f"  {y} cached")
            continue
        try:
            load_calendar_year(latitude, longitude, y, fetch=True)
            ok.append(y)
            print(f"  {y} ok")
        except RuntimeError as exc:
            failed.append(y)
            print(f"  {y} FAILED: {exc}")
        time.sleep(0.25)
    return ok, failed


def load_calendar_year(latitude: float, longitude: float, year: int,
                       *, fetch: bool = True) -> pd.DataFrame:
    """Load one calendar year from cache, downloading on first use."""
    cache = weather_cache_path(latitude, longitude, year)
    if cache.exists():
        return load_weather_cache(cache)
    if not fetch:
        raise FileNotFoundError(f"Weather cache missing: {cache}")
    df = fetch_calendar_year(latitude, longitude, year)
    save_weather_cache(df, cache)
    return df


def parse_weather_config(cfg: dict, house_cfg: dict | None = None) -> dict:
    """Return normalized weather settings from config.toml.

    If *house_cfg* contains ``[location]``, its latitude/longitude override the
    defaults in ``config.toml`` (so ``HOUSE_CONFIG`` selects the weather site).
    """
    w = cfg.get("weather", {})
    wc = w.get("worst_case", {})
    year = w.get("year", 2023)
    if isinstance(year, str) and year.lower() == "tmy":
        year = 0
    lat = float(w.get("latitude", 48.351))
    lon = float(w.get("longitude", 10.164))
    location_label = None
    if house_cfg:
        loc = house_cfg.get("location", {})
        if "latitude" in loc:
            lat = float(loc["latitude"])
        if "longitude" in loc:
            lon = float(loc["longitude"])
        location_label = loc.get("label")
    wc_start = int(wc.get("year_start", 2005))
    wc_end = int(wc.get("year_end", 2023))
    return {
        "year": int(year),
        "latitude": lat,
        "longitude": lon,
        "location_label": location_label,
        "tmy_path": Path(w.get("tmy_path", DEFAULT_TMY)),
        "worst_case_start": wc_start,
        "worst_case_end": wc_end,
        "worst_case_hdh_base_c": float(wc.get("hdh_base_c", 15.0)),
        "worst_case_spell_threshold_c": float(wc.get("spell_threshold_c", 0.0)),
    }


def _location_note(settings: dict) -> str:
    label = settings.get("location_label")
    if label:
        return f", {label}"
    lat, lon = settings["latitude"], settings["longitude"]
    return f" ({lat:.3f}, {lon:.3f})"


def worst_case_cache_path(latitude: float, longitude: float,
                          year_start: int, year_end: int) -> Path:
    return (SOURCE_DIR
            / f"worst_case_year_{latitude:.3f}_{longitude:.3f}"
              f"_{year_start}_{year_end}.csv")


def worst_case_selection_path(latitude: float, longitude: float,
                              year_start: int, year_end: int) -> Path:
    return (SOURCE_DIR
            / f"worst_case_selection_{latitude:.3f}_{longitude:.3f}"
              f"_{year_start}_{year_end}.json")


def longest_spell_below(temps: np.ndarray, threshold: float) -> tuple[int, float]:
    """Longest consecutive hours below *threshold*; return (hours, mean temp)."""
    best_len, best_mean = 0, float("nan")
    i, n = 0, len(temps)
    while i < n:
        if temps[i] >= threshold:
            i += 1
            continue
        j = i
        while j < n and temps[j] < threshold:
            j += 1
        length = j - i
        if length > best_len:
            best_len = length
            best_mean = float(temps[i:j].mean())
        i = j
    return best_len, best_mean


def score_month_segment(temps: np.ndarray, hdh_base_c: float,
                        spell_threshold_c: float) -> dict:
    """Coldness score for one calendar month of hourly temperatures.

    Higher is worse. Compared lexicographically:
      1. heating degree hours (duration × severity)
      2. longest cold spell below spell_threshold_c
      3. lowest hourly temperature
    """
    hdh = float(np.sum(np.maximum(hdh_base_c - temps, 0.0)))
    spell_h, spell_mean = longest_spell_below(temps, spell_threshold_c)
    return {
        "hdh": hdh,
        "spell_hours": spell_h,
        "spell_mean": spell_mean,
        "min_temp": float(np.min(temps)),
        "mean_temp": float(np.mean(temps)),
    }


def _score_sort_key(score: dict) -> tuple:
    return (score["hdh"], score["spell_hours"], -score["min_temp"])


def select_coldest_months(years: dict[int, pd.DataFrame], calendar_month: int,
                          hdh_base_c: float,
                          spell_threshold_c: float) -> tuple[int, dict, pd.DataFrame]:
    """Pick the historical year whose *calendar_month* was coldest."""
    best_year, best_score, best_chunk = None, None, None
    for year, df in years.items():
        chunk = df[df["time"].dt.month == calendar_month]
        if chunk.empty:
            continue
        temps = chunk["t_out"].to_numpy(dtype=float)
        score = score_month_segment(temps, hdh_base_c, spell_threshold_c)
        if best_score is None or _score_sort_key(score) > _score_sort_key(best_score):
            best_year, best_score, best_chunk = year, score, chunk
    if best_year is None:
        raise RuntimeError(f"No data for calendar month {calendar_month}")
    return best_year, best_score, best_chunk.reset_index(drop=True)


def stitch_worst_case_year(selections: dict[int, tuple[int, pd.DataFrame]],
                         synth_year: int = 2001) -> pd.DataFrame:
    """Concatenate selected months into one continuous hourly timeline."""
    parts = []
    hour_offset = 0
    synth_start = pd.Timestamp(f"{synth_year}-01-01", tz="UTC")
    for month in range(1, 13):
        source_year, chunk = selections[month]
        n_hours = len(chunk)
        times = pd.date_range(
            synth_start + pd.Timedelta(hours=hour_offset),
            periods=n_hours,
            freq="h",
            tz="UTC",
        )
        part = pd.DataFrame({
            "time": times,
            "t_out": chunk["t_out"].to_numpy(dtype=float),
            "source_year": source_year,
        })
        parts.append(part)
        hour_offset += n_hours
    return _normalize(pd.concat(parts, ignore_index=True))


def load_available_years(latitude: float, longitude: float,
                         year_start: int, year_end: int,
                         *, fetch: bool = True) -> tuple[dict[int, pd.DataFrame], list[int]]:
    """Load cached/downloaded calendar years; skip years that are unavailable."""
    years: dict[int, pd.DataFrame] = {}
    missing: list[int] = []
    for y in range(year_start, year_end + 1):
        cache = weather_cache_path(latitude, longitude, y)
        if cache.exists():
            years[y] = load_weather_cache(cache)
            continue
        if not fetch:
            missing.append(y)
            continue
        try:
            years[y] = load_calendar_year(latitude, longitude, y, fetch=True)
        except RuntimeError:
            missing.append(y)
    return years, missing


def manifest_from_tmy(df: pd.DataFrame, month_years: dict[int, int],
                      hdh_base_c: float, spell_threshold_c: float) -> dict:
    """Build a selection manifest from an already-stitched TMY dataframe."""
    manifest: dict[str, dict] = {}
    for month, source_year in month_years.items():
        chunk = df[df["month"] == month]
        score = score_month_segment(
            chunk["t_out"].to_numpy(dtype=float), hdh_base_c, spell_threshold_c,
        )
        manifest[str(month)] = {
            "source_year": source_year,
            "hours": len(chunk),
            **{k: round(v, 2) if isinstance(v, float) else v for k, v in score.items()},
        }
    return manifest


def build_worst_case_year(latitude: float, longitude: float,
                          year_start: int = 2005, year_end: int = 2023,
                          hdh_base_c: float = 15.0,
                          spell_threshold_c: float = 0.0,
                          *, fetch: bool = True,
                          tmy_path: Path = DEFAULT_TMY) -> tuple[pd.DataFrame, dict]:
    """Build synthetic worst-case year and a selection manifest."""
    years, missing = load_available_years(
        latitude, longitude, year_start, year_end, fetch=fetch,
    )
    if len(years) < year_end - year_start:
        print(f"  Note: {len(missing)} years unavailable ({missing[:5]}…), "
              f"using {len(years)} cached years for month selection")
    if not years:
        tmy = load_tmy(tmy_path)
        month_years = load_tmy_month_years(tmy_path)
        print("  Falling back to PVGIS TMY stitched months (no calendar-year cache)")
        return tmy.copy(), manifest_from_tmy(
            tmy, month_years, hdh_base_c, spell_threshold_c,
        )
    selections: dict[int, tuple[int, pd.DataFrame]] = {}
    manifest: dict[str, dict] = {}
    for month in range(1, 13):
        source_year, score, chunk = select_coldest_months(
            years, month, hdh_base_c, spell_threshold_c,
        )
        selections[month] = (source_year, chunk)
        manifest[str(month)] = {
            "source_year": source_year,
            "hours": len(chunk),
            **{k: round(v, 2) if isinstance(v, float) else v for k, v in score.items()},
        }
    df = stitch_worst_case_year(selections)
    return df, manifest


def save_worst_case_cache(df: pd.DataFrame, manifest: dict, weather_path: Path,
                          selection_path: Path) -> None:
    weather_path.parent.mkdir(parents=True, exist_ok=True)
    out = df[["time", "t_out", "source_year"]].copy()
    out.to_csv(weather_path, index=False)
    selection_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def load_worst_case_cache(weather_path: Path) -> pd.DataFrame:
    raw = pd.read_csv(weather_path)
    df = pd.DataFrame()
    df["time"] = pd.to_datetime(raw["time"], utc=True)
    df["t_out"] = raw["t_out"].astype(float)
    if "source_year" in raw.columns:
        df["source_year"] = raw["source_year"].astype(int)
    return _normalize(df)


def load_or_build_worst_case_year(settings: dict, *, fetch: bool = True
                                  ) -> tuple[pd.DataFrame, dict]:
    lat = settings["latitude"]
    lon = settings["longitude"]
    y0 = settings["worst_case_start"]
    y1 = settings["worst_case_end"]
    weather_path = worst_case_cache_path(lat, lon, y0, y1)
    selection_path = worst_case_selection_path(lat, lon, y0, y1)
    if weather_path.exists() and selection_path.exists():
        manifest = json.loads(selection_path.read_text(encoding="utf-8"))
        return load_worst_case_cache(weather_path), manifest
    df, manifest = build_worst_case_year(
        lat, lon, y0, y1,
        settings["worst_case_hdh_base_c"],
        settings["worst_case_spell_threshold_c"],
        fetch=fetch,
        tmy_path=settings["tmy_path"],
    )
    save_worst_case_cache(df, manifest, weather_path, selection_path)
    return df, manifest


@dataclass
class WeatherScenario:
    """A named outdoor-temperature scenario for the house model."""

    name: str
    data: pd.DataFrame   # must contain at least 't_out'; full year also has time/day/month
    dt_hours: float = 1.0
    use_inertia: bool = True


class WeatherDriver:
    def __init__(self, cfg: dict | None = None, house_cfg: dict | None = None):
        settings = parse_weather_config(cfg or {}, house_cfg)
        self.settings = settings
        self.tmy_path = settings["tmy_path"]
        self.tmy_df = load_tmy(self.tmy_path)
        y0, y1 = settings["worst_case_start"], settings["worst_case_end"]
        loc = _location_note(settings)
        self.worst_case_source_label = (
            f"synthetic worst-case year ({y0}–{y1}, coldest month each{loc})"
        )
        self.worst_case_title_label = f"worst-case {y0}–{y1}"
        self._worst_case_df: pd.DataFrame | None = None
        self._worst_case_manifest: dict | None = None

        year = settings["year"]
        if year == 0:
            self.source_label = f"PVGIS TMY (2005–2023 stitched months{loc})"
            self.title_label = "TMY"
            self.weather_year = None
            self.df = self.tmy_df
        else:
            self.weather_year = year
            self.source_label = f"calendar year {year} (Open-Meteo ERA5{loc})"
            self.title_label = str(year)
            self.df = load_calendar_year(
                settings["latitude"], settings["longitude"], year,
            )

    @classmethod
    def from_config(cls, cfg: dict, house_cfg: dict | None = None) -> "WeatherDriver":
        return cls(cfg, house_cfg)

    def full_year(self) -> WeatherScenario:
        """The full hourly outdoor temperature series."""
        return WeatherScenario(name="full_year", data=self.df.copy(),
                               dt_hours=1.0, use_inertia=True)

    def worst_case_manifest(self) -> dict:
        """Per-month selection metadata (source year, HDH, cold spell, …)."""
        self._ensure_worst_case()
        return self._worst_case_manifest  # type: ignore[return-value]

    def _ensure_worst_case(self) -> None:
        if self._worst_case_df is None:
            self._worst_case_df, self._worst_case_manifest = load_or_build_worst_case_year(
                self.settings,
            )

    def worst_case_year(self) -> WeatherScenario:
        """Full hourly synthetic year from the coldest month in each calendar month.

        Independent of the calendar year configured for full_year(). Buffer
        inertia is enabled so cold spells propagate realistically.
        """
        self._ensure_worst_case()
        return WeatherScenario(
            name="worst_case_year",
            data=self._worst_case_df.copy(),  # type: ignore[union-attr]
            dt_hours=1.0,
            use_inertia=True,
        )


if __name__ == "__main__":
    import sys

    from home_heat_sim import load_config
    from house_model import load_house_config

    house_cfg = load_house_config()
    settings = parse_weather_config(load_config(), house_cfg)
    if len(sys.argv) > 1 and sys.argv[1] == "fetch_history":
        y0, y1 = settings["worst_case_start"], settings["worst_case_end"]
        lat, lon = settings["latitude"], settings["longitude"]
        loc = settings.get("location_label") or f"{lat}, {lon}"
        years = list(range(y0, y1 + 1))
        sim_year = settings["year"]
        if sim_year != 0:
            years.append(sim_year)
        print(f"Location: {loc}")
        print(f"Fetching calendar years {y0}–{y1}"
              f"{f' (+ sim year {sim_year})' if sim_year != 0 else ''} …")
        ok, failed = fetch_weather_years(lat, lon, years)
        wc_path = worst_case_cache_path(lat, lon, y0, y1)
        sel_path = worst_case_selection_path(lat, lon, y0, y1)
        if wc_path.exists():
            wc_path.unlink()
        if sel_path.exists():
            sel_path.unlink()
        print(f"Fetched {len(ok)} years; {len(failed)} failed.")
        if sim_year != 0 and sim_year not in ok:
            print(f"ERROR: configured sim year {sim_year} is not available.")
            sys.exit(1)
        if not ok:
            print("ERROR: no weather years available.")
            sys.exit(1)
        if failed:
            print("Warning: some years failed — worst-case may use partial data.")
        print("Cleared worst-case cache — re-run simulation to rebuild.")
        sys.exit(0)

    drv = WeatherDriver.from_config(load_config(), house_cfg)
    yr = drv.full_year().data
    print(f"Weather source: {drv.source_label}")
    print(f"Loaded {len(yr)} hourly records "
          f"({yr['time'].min()} … {yr['time'].max()})")
    print(f"Outdoor temp: min {yr['t_out'].min():.1f} °C, "
          f"mean {yr['t_out'].mean():.1f} °C, max {yr['t_out'].max():.1f} °C\n")
    print("Worst-case month selection:")
    manifest = drv.worst_case_manifest()
    for m in range(1, 13):
        info = manifest[str(m)]
        print(f"  month {m:>2}: {info['source_year']}  "
              f"HDH={info['hdh']:.0f}  spell={info['spell_hours']}h  "
              f"min={info['min_temp']:.1f}°C")
