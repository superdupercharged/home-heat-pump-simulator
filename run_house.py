"""Run the house thermal model on weather data (no heat pump yet).

Usage:
    .venv/bin/python run_house.py [full_year|worst_case]

Outputs a summary table to the terminal and saves a plot to output/.
The house model is steady-state: it reports the power needed to *maintain*
setpoints, not the energy to heat the house up from cold.
"""

from __future__ import annotations

import calendar
import sys
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")  # headless: render straight to file
import matplotlib.pyplot as plt  # noqa: E402

from house_model import House, load_house_config  # noqa: E402
from weather import WeatherDriver  # noqa: E402

OUTPUT_DIR = Path(__file__).with_name("output")


def run_full_year(house: House, scenario) -> Path:
    df = scenario.data
    outdoor = df["t_out"].to_numpy()
    active = house.heating_active(df["time"])
    res = house.power_series(outdoor, dt_hours=scenario.dt_hours,
                             use_inertia=scenario.use_inertia, active=active)
    power_kw = res["total_w"] / 1000.0

    annual_kwh = power_kw.sum() * scenario.dt_hours
    peak_kw = power_kw.max()
    peak_i = int(power_kw.argmax())

    print("HOUSE MODEL - FULL YEAR (steady-state maintenance power)\n")
    print(f"  Annual heat demand : {annual_kwh:,.0f} kWh")
    print(f"  Peak load          : {peak_kw:.2f} kW "
          f"(at {df['t_out'].iloc[peak_i]:.1f} °C outdoor, "
          f"{df['time'].iloc[peak_i]})")
    print(f"  Average load        : {power_kw.mean():.2f} kW\n")

    day = df["day"].to_numpy()
    months = df["month"].to_numpy()
    print(f"  {'Month':<5}{'Energy':>10}{'Mean':>8}{'Peak':>8}{'T_out avg':>11}")
    for m in range(1, 13):
        mask = months == m
        if not mask.any():
            continue
        e = power_kw[mask].sum() * scenario.dt_hours
        print(f"  {calendar.month_abbr[m]:<5}{e:>8.0f}kWh"
              f"{power_kw[mask].mean():>6.2f}kW{power_kw[mask].max():>6.2f}kW"
              f"{outdoor[mask].mean():>9.1f}°C")

    n_days = int(day.max()) + 1
    daily_mean = np.array([power_kw[day == d].mean() for d in range(n_days)])

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7))
    ax1.fill_between(range(n_days), daily_mean, color="#cfe3ff")
    ax1.plot(range(n_days), daily_mean, color="#1f6feb", lw=1.2)
    ax1.set_title("House heating power - daily mean over the year")
    ax1.set_xlabel("day of year")
    ax1.set_ylabel("power (kW)")
    ax1.set_xlim(0, n_days - 1)
    ax1.grid(alpha=0.3)

    sorted_kw = np.sort(power_kw)[::-1]
    ax2.plot(range(len(sorted_kw)), sorted_kw, color="#d29922", lw=1.5)
    ax2.axhline(peak_kw, color="#cf222e", ls="--", lw=0.8,
                label=f"peak {peak_kw:.1f} kW")
    ax2.set_title("Load duration curve (hours at or above a given power)")
    ax2.set_xlabel("hours per year")
    ax2.set_ylabel("power (kW)")
    ax2.set_xlim(0, len(sorted_kw))
    ax2.grid(alpha=0.3)
    ax2.legend()

    fig.tight_layout()
    out = OUTPUT_DIR / "house_full_year.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def run_worst_case(house: House, scenario) -> Path:
    df = scenario.data
    outdoor = df["t_out"].to_numpy()
    active = house.heating_active(df["time"])
    res = house.power_series(outdoor, use_inertia=scenario.use_inertia, active=active)
    power_kw = res["total_w"] / 1000.0

    print("HOUSE MODEL - WORST CASE PER MONTH (coldest hour of each month)\n")
    print(f"  {'Month':<6}{'T_out':>7}{'Basement':>10}{'Attic':>8}{'Power':>9}")
    for i, (_, r) in enumerate(df.iterrows()):
        m = int(r["month"])
        print(f"  {calendar.month_abbr[m]:<6}{outdoor[i]:>6.1f}°C"
              f"{res['t_basement'][i]:>8.1f}°C{res['t_attic'][i]:>6.1f}°C"
              f"{power_kw[i]:>7.2f}kW")
    worst_i = int(power_kw.argmax())
    print(f"\n  Worst month: {calendar.month_abbr[int(df['month'].iloc[worst_i])]}"
          f" at {outdoor[worst_i]:.1f} °C -> {power_kw[worst_i]:.2f} kW")

    months = [calendar.month_abbr[int(m)] for m in df["month"]]
    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(months, power_kw, color="#1f6feb")
    bars[worst_i].set_color("#cf222e")
    for i, v in enumerate(power_kw):
        ax.text(i, v + 0.05, f"{v:.1f}", ha="center", va="bottom", fontsize=8)
    ax.set_title("Worst-case heating power per month (coldest hour)")
    ax.set_ylabel("power (kW)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out = OUTPUT_DIR / "house_worst_case.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def main() -> None:
    scenario_name = sys.argv[1] if len(sys.argv) > 1 else "full_year"
    OUTPUT_DIR.mkdir(exist_ok=True)
    house = House.from_config(load_house_config())
    drv = WeatherDriver()

    if scenario_name == "worst_case":
        out = run_worst_case(house, drv.worst_case_per_month())
    else:
        out = run_full_year(house, drv.full_year())

    print(f"\nPlot saved to: {out}")


if __name__ == "__main__":
    main()
