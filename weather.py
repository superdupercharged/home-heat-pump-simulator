"""Weather driver for the heat pump simulator.

Reads a PVGIS TMY (Typical Meteorological Year) CSV and exposes two scenarios:

  - full_year()            : the full 8760-hour outdoor temperature series
  - worst_case_per_month() : the single coldest hour of each month (12 points),
                             a quick "worst case" stress test

The PVGIS file has a metadata header, then a data block starting at the
``time(UTC),T2m,...`` line, then a provenance footer. T2m is the 2 m air
temperature in °C.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_TMY = Path(__file__).with_name("source_data") / "tmy_48.351_10.164_2005_2023.csv"


def load_tmy(path: Path = DEFAULT_TMY) -> pd.DataFrame:
    """Parse a PVGIS TMY CSV into a tidy hourly DataFrame.

    Returns columns: ``time`` (datetime), ``month`` (1-12), ``hour`` (0-based
    running index), ``day`` (0-based running day index), ``t_out`` (°C).
    """
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
    df = df.reset_index(drop=True)
    df["hour"] = df.index            # running hour index 0..8759 (calendar order)
    df["day"] = df["hour"] // 24     # running day index 0..364
    df["month"] = df["time"].dt.month
    return df


@dataclass
class WeatherScenario:
    """A named outdoor-temperature scenario for the house model."""

    name: str
    data: pd.DataFrame   # must contain at least 't_out'; full year also has time/day/month
    dt_hours: float = 1.0
    use_inertia: bool = True


class WeatherDriver:
    def __init__(self, path: Path = DEFAULT_TMY):
        self.df = load_tmy(path)

    def full_year(self) -> WeatherScenario:
        """The full hourly outdoor temperature series (8760 h)."""
        return WeatherScenario(name="full_year", data=self.df.copy(),
                               dt_hours=1.0, use_inertia=True)

    def worst_case_per_month(self) -> WeatherScenario:
        """Coldest hour of each month (12 points).

        These points are discontinuous in time, so buffer-temperature inertia
        is disabled for this scenario (each point is treated as steady-state).
        """
        idx = self.df.groupby("month")["t_out"].idxmin()
        wc = self.df.loc[idx].sort_values("month").reset_index(drop=True)
        return WeatherScenario(name="worst_case_per_month", data=wc,
                               dt_hours=1.0, use_inertia=False)


if __name__ == "__main__":
    drv = WeatherDriver()
    yr = drv.full_year().data
    print(f"Loaded {len(yr)} hourly records "
          f"({yr['time'].min()} ... {yr['time'].max()})")
    print(f"Outdoor temp: min {yr['t_out'].min():.1f} °C, "
          f"mean {yr['t_out'].mean():.1f} °C, max {yr['t_out'].max():.1f} °C\n")
    print("Coldest hour per month:")
    wc = drv.worst_case_per_month().data
    for _, r in wc.iterrows():
        print(f"  month {int(r['month']):>2}: {r['t_out']:>6.1f} °C  ({r['time']})")
