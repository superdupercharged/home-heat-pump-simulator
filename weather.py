"""Weather driver for the heat pump simulator.

Two data sources (selected via ``[weather]`` in ``config/config.toml``):

  - **Calendar year** (``year = 2023``): hourly 2 m air temperature from
    Open-Meteo ERA5 archive for the configured lat/lon. Cached under
    ``source_data/weather_{lat}_{lon}_{year}.csv`` after the first fetch.
  - **PVGIS TMY** (``year = 0``): stitched Typical Meteorological Year from
    ``source_data/tmy_48.351_10.164_2005_2023.csv``.

Both expose:

  - full_year()            : the full hourly outdoor temperature series
  - worst_case_per_month() : coldest hour of each month from the PVGIS TMY
                             (2005–2023 stitched months), always — independent
                             of the calendar year used for full_year()
"""

from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

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


def load_tmy(path: Path = DEFAULT_TMY) -> pd.DataFrame:
    """Parse a PVGIS TMY CSV into a tidy hourly DataFrame."""
    lines = Path(path).read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("time(UTC)"))
    end = start + 1
    while end < len(lines) and lines[end].strip() and "," in lines[end]:
        end += 1
    block = "\n".join(lines[start:end])
    raw = pd.read_csv(io.StringIO(block))

    df = pd.DataFrame()
    df["time"] = pd.to_datetime(raw["time(UTC)"], format="%Y%m%d:%H%M")
    df["t_out"] = raw["T2m"].astype(float)
    return _normalize(df)


def weather_cache_path(latitude: float, longitude: float, year: int) -> Path:
    return SOURCE_DIR / f"weather_{latitude:.3f}_{longitude:.3f}_{year}.csv"


def fetch_calendar_year(latitude: float, longitude: float, year: int) -> pd.DataFrame:
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
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            payload = json.load(resp)
    except urllib.error.URLError as exc:
        cache = weather_cache_path(latitude, longitude, year)
        raise RuntimeError(
            f"Could not download weather for {year} ({latitude}, {longitude}): {exc}\n"
            f"If you are offline, place a cached CSV at {cache}"
        ) from exc

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


def parse_weather_config(cfg: dict) -> dict:
    """Return normalized weather settings from config.toml."""
    w = cfg.get("weather", {})
    year = w.get("year", 2023)
    if isinstance(year, str) and year.lower() == "tmy":
        year = 0
    return {
        "year": int(year),
        "latitude": float(w.get("latitude", 48.351)),
        "longitude": float(w.get("longitude", 10.164)),
        "tmy_path": Path(w.get("tmy_path", DEFAULT_TMY)),
    }


@dataclass
class WeatherScenario:
    """A named outdoor-temperature scenario for the house model."""

    name: str
    data: pd.DataFrame   # must contain at least 't_out'; full year also has time/day/month
    dt_hours: float = 1.0
    use_inertia: bool = True


class WeatherDriver:
    def __init__(self, cfg: dict | None = None):
        settings = parse_weather_config(cfg or {})
        self.tmy_path = settings["tmy_path"]
        self.tmy_df = load_tmy(self.tmy_path)
        self.worst_case_source_label = "PVGIS TMY (2005–2023, coldest hour per month)"
        self.worst_case_title_label = "TMY 2005–2023"

        year = settings["year"]
        if year == 0:
            self.source_label = "PVGIS TMY (2005–2023 stitched months)"
            self.title_label = "TMY"
            self.weather_year = None
            self.df = self.tmy_df
        else:
            self.weather_year = year
            self.source_label = f"calendar year {year} (Open-Meteo ERA5)"
            self.title_label = str(year)
            self.df = load_calendar_year(
                settings["latitude"], settings["longitude"], year,
            )

    @classmethod
    def from_config(cls, cfg: dict) -> "WeatherDriver":
        return cls(cfg)

    def full_year(self) -> WeatherScenario:
        """The full hourly outdoor temperature series."""
        return WeatherScenario(name="full_year", data=self.df.copy(),
                               dt_hours=1.0, use_inertia=True)

    def worst_case_per_month(self) -> WeatherScenario:
        """Coldest hour of each month (12 points) from the PVGIS TMY.

        Always uses the 2005–2023 stitched TMY, not the calendar year
        configured for full_year(). Points are discontinuous in time, so
        buffer-temperature inertia is disabled (steady-state per point).
        """
        idx = self.tmy_df.groupby("month")["t_out"].idxmin()
        wc = self.tmy_df.loc[idx].sort_values("month").reset_index(drop=True)
        return WeatherScenario(name="worst_case_per_month", data=wc,
                               dt_hours=1.0, use_inertia=False)


if __name__ == "__main__":
    from home_heat_sim import load_config

    drv = WeatherDriver.from_config(load_config())
    yr = drv.full_year().data
    print(f"Weather source: {drv.source_label}")
    print(f"Loaded {len(yr)} hourly records "
          f"({yr['time'].min()} … {yr['time'].max()})")
    print(f"Outdoor temp: min {yr['t_out'].min():.1f} °C, "
          f"mean {yr['t_out'].mean():.1f} °C, max {yr['t_out'].max():.1f} °C\n")
    print("Coldest hour per month:")
    wc = drv.worst_case_per_month().data
    for _, r in wc.iterrows():
        print(f"  month {int(r['month']):>2}: {r['t_out']:>6.1f} °C  ({r['time']})")
