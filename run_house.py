"""Run the house thermal model on weather data (no heat pump yet).

Usage:
    .venv/bin/python run_house.py [full_year|worst_case]

Outputs a summary table to the terminal and saves a plot to outputs/<house>/.
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

from house_model import House, house_output_dir, load_house_config  # noqa: E402
from home_heat_sim import load_config  # noqa: E402
from weather import WeatherDriver  # noqa: E402


def run_full_year(house: House, scenario, weather_label: str = "") -> Path:
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
    weather_note = f"{weather_label}  |  " if weather_label else ""
    ax1.set_title(f"House heating power - daily mean  |  {weather_note}{n_days} days")
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
    out = house_output_dir() / "house_full_year.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def _monthly_peak_indices(months: np.ndarray, values: np.ndarray) -> list[int]:
    peaks = []
    for m in range(1, 13):
        mask = months == m
        if not mask.any():
            continue
        local = np.where(mask)[0]
        peaks.append(int(local[np.argmax(values[mask])]))
    return peaks


def run_worst_case(house: House, scenario, weather_label: str = "",
                   manifest: dict | None = None) -> Path:
    df = scenario.data
    outdoor = df["t_out"].to_numpy()
    months_arr = df["month"].to_numpy()
    active = house.heating_active(df["time"])
    res = house.power_series(outdoor, use_inertia=scenario.use_inertia, active=active)
    power_kw = res["total_w"] / 1000.0

    print("HOUSE MODEL - WORST CASE YEAR\n")
    if manifest:
        print("  Selected coldest calendar month from each historical year:")
        print(f"  {'Mo':<4}{'Year':>6}{'HDH':>8}{'Spell':>7}{'T_min':>7}")
        for m in range(1, 13):
            info = manifest[str(m)]
            print(f"  {calendar.month_abbr[m]:<4}{info['source_year']:>6}"
                  f"{info['hdh']:>8.0f}{info['spell_hours']:>5}h"
                  f"{info['min_temp']:>6.1f}°C")
        print()

    peak_idx = _monthly_peak_indices(months_arr, res["total_w"])
    print(f"  {'Month':<6}{'SrcYr':>6}{'T_out':>7}{'Basement':>10}{'Attic':>8}{'Power':>9}")
    month_labels, peak_kw = [], []
    for i in peak_idx:
        m = int(months_arr[i])
        src = int(df["source_year"].iloc[i]) if "source_year" in df.columns else 0
        month_labels.append(calendar.month_abbr[m])
        peak_kw.append(power_kw[i])
        print(f"  {calendar.month_abbr[m]:<6}{src:>6}{outdoor[i]:>6.1f}°C"
              f"{res['t_basement'][i]:>8.1f}°C{res['t_attic'][i]:>6.1f}°C"
              f"{power_kw[i]:>7.2f}kW")
    worst_i = int(np.argmax(peak_kw))
    print(f"\n  Peak month: {month_labels[worst_i]}"
          f" at {outdoor[peak_idx[worst_i]]:.1f} °C -> {peak_kw[worst_i]:.2f} kW")

    fig, ax = plt.subplots(figsize=(10, 5.5))
    bars = ax.bar(month_labels, peak_kw, color="#1f6feb")
    bars[worst_i].set_color("#cf222e")
    for i, v in enumerate(peak_kw):
        if v > 0.1:
            ax.text(i, v + 0.12, f"{v:.1f}", ha="center", va="bottom", fontsize=8)
    weather_note = f"{weather_label}  ·  " if weather_label else ""
    ax.set_title(
        "Worst-case year — peak heating power per month\n"
        f"{weather_note}coldest month each from historical range",
        fontsize=10,
        linespacing=1.35,
    )
    ax.set_ylabel("power (kW)")
    peak = float(max(peak_kw) if peak_kw else 0)
    y_top = int(np.ceil((peak + 1.2) / 2)) * 2
    ax.set_ylim(0, max(y_top, 10))
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    out = house_output_dir() / "house_worst_case.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def main() -> None:
    scenario_name = sys.argv[1] if len(sys.argv) > 1 else "full_year"
    house_output_dir().mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    house_cfg = load_house_config()
    house = House.from_config(house_cfg)
    drv = WeatherDriver.from_config(cfg, house_cfg)
    print(f"Weather: {drv.source_label}")

    if scenario_name == "worst_case":
        print(f"Weather (worst case): {drv.worst_case_source_label}")
        out = run_worst_case(
            house, drv.worst_case_year(), drv.worst_case_title_label,
            drv.worst_case_manifest(),
        )
    else:
        out = run_full_year(house, drv.full_year(), drv.title_label)

    print(f"\nPlot saved to: {out}")


if __name__ == "__main__":
    main()
