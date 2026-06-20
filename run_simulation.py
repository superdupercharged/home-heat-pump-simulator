"""Couple the house thermal model with the Midea heat pump COP model.

Usage:
    .venv/bin/python run_simulation.py [full_year|worst_case]

For each hour:
  1. the house model gives the thermal power needed to hold setpoints (P_th_demand)
  2. the heat pump model gives, at that outdoor temp and the configured flow
     temperature, the COP and the max thermal capacity (P_th_max)
  3. the heat pump covers min(demand, capacity); any shortfall is met by the
     resistive backup heater (COP = 1)

Outputs annual electricity, seasonal COP (SCOP), peak draw, backup usage,
a monthly table and plots in outputs/<house>/.
"""

from __future__ import annotations

import calendar
import os
import sys
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

from heating_curve import HeatingCurve, plot_yearly_profile  # noqa: E402
from home_heat_sim import FittedHeatPump, HeatPumpSpec, load_config  # noqa: E402
from house_model import House, house_output_dir, load_house_config  # noqa: E402
from weather import WeatherDriver  # noqa: E402


def couple(house: House, hp: FittedHeatPump, scenario,
           heating_curve: HeatingCurve) -> dict:
    """Run the coupled house + heat pump model over a weather scenario."""
    outdoor = scenario.data["t_out"].to_numpy()
    active = house.heating_active(scenario.data["time"])
    hs = house.power_series(outdoor, dt_hours=scenario.dt_hours,
                            use_inertia=scenario.use_inertia, active=active)
    demand = hs["total_w"]
    transmission_w = hs["envelope_w"] + hs["floor_w"] + hs["ceiling_w"]
    infiltration_w = hs["infiltration_w"]   # leaky envelope, every room
    airing_w = hs["airing_w"]               # window Stoßlüften only

    flow_data = heating_curve.flow_temp_series(outdoor, dt_hours=scenario.dt_hours)
    flow_series = flow_data["flow"]
    hpres = hp.simulate_series(outdoor, flow_series)
    cop = hpres["COP"]
    capacity = hpres["P_th"]

    delivered_hp = np.minimum(demand, capacity)
    p_el_hp = np.divide(delivered_hp, cop, out=np.zeros_like(demand), where=cop > 0)
    backup_th = np.clip(demand - capacity, 0.0, None)   # resistive, COP = 1
    p_el_total = p_el_hp + backup_th

    return {"outdoor": outdoor, "demand_w": demand, "cop": cop,
            "capacity_w": capacity, "delivered_hp_w": delivered_hp,
            "p_el_hp_w": p_el_hp,
            "backup_w": backup_th, "p_el_total_w": p_el_total,
            "transmission_w": transmission_w,
            "infiltration_w": infiltration_w, "airing_w": airing_w,
            "flow_c": flow_series, "outdoor_damped": flow_data["outdoor_damped"]}


def compute_dhw(house_cfg, hp, df):
    """Monthly domestic hot-water heat & electricity from the water profile."""
    hw = house_cfg.get("hot_water")
    if not hw:
        return None
    from water import hot_water_energy_kwh, load_monthly_water_m3

    monthly = hot_water_energy_kwh(load_monthly_water_m3(), hw)
    flow = float(hw.get("dhw_flow_temp_c", 55.0))
    means = df.groupby("month")["t_out"].mean()
    rows, tot_heat, tot_el = {}, 0.0, 0.0
    for m, d in monthly.items():
        t_out = float(means.get(m, df["t_out"].mean()))
        cop = float(hp.simulate_series([t_out], flow)["COP"][0])
        el = d["heat_kwh"] / cop if cop else d["heat_kwh"]
        rows[m] = {**d, "cop": cop, "elec_kwh": el, "t_out": t_out}
        tot_heat += d["heat_kwh"]
        tot_el += el
    return {"rows": rows, "heat_kwh": tot_heat, "elec_kwh": tot_el}


def run_full_year(house, hp, scenario, heating_curve, price, house_label,
                  dhw=None, weather_label: str = "") -> Path:
    df = scenario.data
    r = couple(house, hp, scenario, heating_curve)
    dt = scenario.dt_hours

    th_kwh = r["demand_w"].sum() * dt / 1000
    el_kwh = r["p_el_total_w"].sum() * dt / 1000
    hp_el_kwh = r["p_el_hp_w"].sum() * dt / 1000
    backup_kwh = r["backup_w"].sum() * dt / 1000
    scop = th_kwh / el_kwh if el_kwh else float("nan")
    peak_el = r["p_el_total_w"].max() / 1000
    backup_hours = int((r["backup_w"] > 1).sum())
    yearly_cost = el_kwh * price
    monthly_cost = yearly_cost / 12

    heating = r["demand_w"] > 1
    avg_flow = float(r["flow_c"][heating].mean()) if heating.any() else float("nan")

    print("COUPLED HOUSE + HEAT PUMP - FULL YEAR")
    print(f"  House config        : {house_label}"
          f"{'  (no DHW/ventilation configured)' if not dhw else ''}")
    print(f"  {heating_curve.label()}")
    if house.circulation_proxy_m2:
        for fl, area in sorted(house.circulation_proxy_m2.items()):
            print(f"  Circulation proxy {fl}: {area:.1f} m² "
                  f"(auto: net floor − modeled rooms)")
    if heating.any():
        print(f"  Avg flow (heating)  : {avg_flow:.1f} °C\n")
    else:
        print()
    print(f"  Heat delivered      : {th_kwh:,.0f} kWh")
    print(f"  JAZ / year-COP      : {scop:.2f}")
    print(f"  Heat pump elec.     : {hp_el_kwh:,.0f} kWh")
    print(f"  Backup elec.        : {backup_kwh:,.0f} kWh")
    print(f"  Total electricity   : {el_kwh:,.0f} kWh")
    print(f"  Peak electrical draw: {peak_el:.2f} kW")
    print(f"  Backup heater       : {backup_kwh / th_kwh * 100:.1f}% of heat, "
          f"{backup_hours} h/year")

    dhw_el = dhw["elec_kwh"] if dhw else 0.0
    dhw_heat = dhw["heat_kwh"] if dhw else 0.0
    if dhw:
        dhw_scop = dhw_heat / dhw_el if dhw_el else float("nan")
        print(f"\n  --- Hot water (DHW) ---")
        print(f"  DHW heat            : {dhw_heat:,.0f} kWh")
        print(f"  DHW electricity     : {dhw_el:,.0f} kWh (avg COP {dhw_scop:.2f})")

    total_el = el_kwh + dhw_el
    total_cost = total_el * price
    print(f"\n  === Combined (heating + DHW) ===")
    print(f"  Total electricity   : {total_el:,.0f} kWh")
    print(f"  Electricity bill    : {total_cost:,.0f} EUR/year "
          f"(~{total_cost / 12:,.0f} EUR/month avg) @ {price:.2f} EUR/kWh\n")

    months = df["month"].to_numpy()
    print(f"  {'Month':<5}{'Heat':>9}{'Elec':>9}{'COP':>6}{'Backup':>9}")
    for m in range(1, 13):
        mask = months == m
        if not mask.any():
            continue
        th = r["demand_w"][mask].sum() * dt / 1000
        el = r["p_el_total_w"][mask].sum() * dt / 1000
        bk = r["backup_w"][mask].sum() * dt / 1000
        print(f"  {calendar.month_abbr[m]:<5}{th:>7.0f}kWh{el:>7.0f}kWh"
              f"{(th / el if el else 0):>6.2f}{bk:>7.0f}kWh")

    day = df["day"].to_numpy()
    n_days = int(day.max()) + 1
    th_day = np.array([r["demand_w"][day == d].sum() * dt / 1000 for d in range(n_days)])
    el_day = np.array([r["p_el_total_w"][day == d].sum() * dt / 1000 for d in range(n_days)])

    # Spread the monthly DHW energy evenly over the days (DHW runs year-round).
    label_suffix = ""
    if dhw:
        day_month = df.groupby("day")["month"].first().to_numpy()
        days_per_month = {m: int((day_month == m).sum()) for m in set(day_month)}
        dhw_heat_day = np.array([dhw["rows"].get(m, {}).get("heat_kwh", 0.0)
                                 / days_per_month[m] for m in day_month])
        dhw_el_day = np.array([dhw["rows"].get(m, {}).get("elec_kwh", 0.0)
                               / days_per_month[m] for m in day_month])
        th_day = th_day + dhw_heat_day
        el_day = el_day + dhw_el_day
        label_suffix = " (incl. DHW)"

    # Annual thermal-energy split for the pie chart. "Lüften" here covers both
    # ventilation forms: window airing + baseline infiltration (undichte Hülle).
    pos = r["demand_w"] > 0
    trans_kwh = r["transmission_w"][pos].sum() * dt / 1000
    vent_kwh = (r["infiltration_w"][pos].sum() + r["airing_w"][pos].sum()) * dt / 1000

    fig = plt.figure(figsize=(13, 8))
    grid = fig.add_gridspec(2, 3, height_ratios=[2.0, 1.0], width_ratios=[1.0, 1.0, 1.35])
    ax1 = fig.add_subplot(grid[0, :])
    ax_cop = fig.add_subplot(grid[1, 0])
    ax_pie = fig.add_subplot(grid[1, 1])
    ax_info = fig.add_subplot(grid[1, 2])
    ax_info.axis("off")

    # --- Top: daily energy (full width, undisturbed) ---
    ax1.fill_between(range(n_days), th_day, color="#ffd8a8",
                     label="heat delivered" + label_suffix)
    ax1.plot(range(n_days), el_day, color="#1f6feb", lw=1.2,
             label="electricity used" + label_suffix)
    flow_note = f"avg VL {avg_flow:.0f}°C" if heating.any() else heating_curve.label()
    weather_note = f"{weather_label}  |  " if weather_label else ""
    ax1.set_title(f"{weather_note}Daily energy: heat vs electricity  |  "
                  f"house: {house_label}  |  {flow_note}, SCOP {scop:.2f}")
    ax1.set_ylabel("energy (kWh/day)")
    ax1.set_xlim(0, n_days - 1)
    month_starts = df.groupby("month")["day"].min()
    ax1.set_xticks([month_starts[m] for m in range(1, 13) if m in month_starts.index])
    ax1.set_xticklabels([calendar.month_abbr[m] for m in range(1, 13)
                         if m in month_starts.index])
    ax1.grid(alpha=0.3)
    ax1.legend(loc="upper center")

    # --- Bottom-left: small system-COP scatter ---
    heating = r["demand_w"] > 1
    sys_cop = np.divide(r["demand_w"], r["p_el_total_w"],
                        out=np.zeros_like(r["demand_w"]), where=r["p_el_total_w"] > 0)
    ax_cop.scatter(r["outdoor"][heating], sys_cop[heating], s=4,
                   c=r["backup_w"][heating] > 1, cmap="coolwarm", alpha=0.5)
    ax_cop.axhline(1.0, color="#cf222e", ls="--", lw=0.7)
    ax_cop.set_title("System COP vs outdoor T", fontsize=9)
    ax_cop.set_xlabel("outdoor °C", fontsize=8)
    ax_cop.set_ylabel("COP", fontsize=8)
    ax_cop.tick_params(labelsize=7)
    ax_cop.grid(alpha=0.3)

    # --- Bottom-middle: energy-split pie (thermal kWh) ---
    pie_vals = [trans_kwh, vent_kwh]
    pie_labels = ["Transmission loss", "Ventilation\n(airing + infiltr.)"]
    pie_colors = ["#d29922", "#1f6feb"]
    if dhw and dhw_heat > 0:
        pie_vals.append(dhw_heat)
        pie_labels.append("Hot water (DHW)")
        pie_colors.append("#2da44e")
    ax_pie.pie(pie_vals, labels=pie_labels, colors=pie_colors,
               autopct="%1.0f%%", startangle=90, textprops={"fontsize": 8})
    ax_pie.set_title("Energy split (heat)", fontsize=9)

    # --- Bottom-right: info box ---
    total_heat = th_kwh + dhw_heat
    total_scop = total_heat / total_el if total_el else float("nan")
    dhw_heat_line = f"DHW heat:      {dhw_heat:>7,.0f} kWh\n" if dhw else ""
    dhw_el_line = f"DHW elec.:     {dhw_el:>7,.0f} kWh\n" if dhw else ""
    avg_flow_line = (
        f"Avg Vorlauf:   {avg_flow:>7.1f} °C\n" if heating.any() else ""
    )
    info_left = (
        f"--- Heat required ---\n"
        f"Heating:       {th_kwh:>7,.0f} kWh\n"
        + dhw_heat_line +
        f"Total heat:    {total_heat:>7,.0f} kWh\n"
        f"\n--- Electricity ---\n"
        f"Heat pump el.: {hp_el_kwh:>7,.0f} kWh\n"
        f"Backup el.:    {backup_kwh:>7,.0f} kWh\n"
        f"Heating el.:   {el_kwh:>7,.0f} kWh\n"
        + dhw_el_line +
        f"Total elec.:   {total_el:>7,.0f} kWh"
    )
    info_right = (
        f"JAZ (heating): {scop:.2f}\n"
        f"JAZ (total):   {total_scop:.2f}\n"
        + avg_flow_line +
        f"Peak draw:     {peak_el:>5.1f} kW\n"
        f"\nBill @ {price:.2f} EUR/kWh:\n"
        f"  {total_cost:,.0f} EUR/yr\n"
        f"  ({total_cost / 12:,.0f} EUR/mo)"
    )
    text_kw = dict(fontsize=10, family="monospace", linespacing=1.3,
                   transform=ax_info.transAxes, ha="left", va="top")
    ax_info.add_patch(FancyBboxPatch(
        (0.0, 0.0), 1.0, 1.0, boxstyle="round,pad=0.02",
        transform=ax_info.transAxes, fc="white", ec="#1f6feb", alpha=0.92,
        clip_on=False,
    ))
    ax_info.text(0.04, 0.96, info_left, **text_kw)
    ax_info.text(0.54, 0.96, info_right, **text_kw)

    fig.tight_layout()
    out = house_output_dir() / "sim_full_year.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)

    yearly_out = plot_yearly_profile(
        heating_curve, df, r,
        path=house_output_dir() / "sim_yearly_temps.png",
        weather_label=weather_label,
    )
    print(f"Yearly temp plot    : {yearly_out}")
    return out


def _print_worst_case_selection(manifest: dict) -> None:
    print("  Selected coldest calendar month from each historical year:")
    print(f"  {'Mo':<4}{'Year':>6}{'HDH':>8}{'Spell':>7}{'T_min':>7}{'T_mean':>7}")
    for m in range(1, 13):
        info = manifest[str(m)]
        print(f"  {calendar.month_abbr[m]:<4}{info['source_year']:>6}"
              f"{info['hdh']:>8.0f}{info['spell_hours']:>5}h"
              f"{info['min_temp']:>6.1f}°C{info['mean_temp']:>6.1f}°C")


def _monthly_peak_indices(months: np.ndarray, values: np.ndarray) -> list[int]:
    """Index of the maximum *values* hour in each calendar month."""
    peaks = []
    for m in range(1, 13):
        mask = months == m
        if not mask.any():
            continue
        local = np.where(mask)[0]
        peaks.append(int(local[np.argmax(values[mask])]))
    return peaks


def run_worst_case(house, hp, scenario, heating_curve, house_label,
                   weather_label: str = "", manifest: dict | None = None) -> Path:
    df = scenario.data
    r = couple(house, hp, scenario, heating_curve)
    months_arr = df["month"].to_numpy()

    print("COUPLED HOUSE + HEAT PUMP - WORST CASE YEAR")
    print(f"  House config        : {house_label}")
    print(f"  {heating_curve.label()}")
    if manifest:
        _print_worst_case_selection(manifest)
    print()

    peak_idx = _monthly_peak_indices(months_arr, r["p_el_total_w"])
    print(f"  {'Month':<6}{'SrcYr':>6}{'T_out':>7}{'VL':>6}{'Demand':>9}{'COP':>6}"
          f"{'Capacity':>10}{'Elec':>8}{'Backup':>8}")
    month_labels = []
    el_peak, hp_peak, bk_peak, cop_peak = [], [], [], []
    for i in peak_idx:
        m = int(months_arr[i])
        src = int(df["source_year"].iloc[i]) if "source_year" in df.columns else 0
        month_labels.append(calendar.month_abbr[m])
        el = r["p_el_total_w"][i] / 1000
        hp = r["p_el_hp_w"][i] / 1000
        bk = r["backup_w"][i] / 1000
        el_peak.append(el)
        hp_peak.append(hp)
        bk_peak.append(bk)
        cop_peak.append(r["cop"][i])
        print(f"  {calendar.month_abbr[m]:<6}{src:>6}{r['outdoor'][i]:>6.1f}°C"
              f"{r['flow_c'][i]:>5.0f}°C"
              f"{r['demand_w'][i] / 1000:>7.2f}kW{r['cop'][i]:>6.2f}"
              f"{r['capacity_w'][i] / 1000:>8.2f}kW{el:>6.2f}kW{bk:>6.2f}kW")

    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.bar(month_labels, hp_peak, color="#1f6feb", label="heat pump electricity")
    ax.bar(month_labels, bk_peak, bottom=hp_peak, color="#cf222e",
           label="backup heater")
    for i, v in enumerate(el_peak):
        if v > 0.1:
            ax.text(i, v + 0.12, f"{cop_peak[i]:.1f}", ha="center", va="bottom",
                    fontsize=8, color="#444")

    weather_note = f"{weather_label}  ·  " if weather_label else ""
    ax.set_title(
        "Worst-case year — peak electrical draw per month\n"
        f"{weather_note}{heating_curve.label()}  ·  {house_label}",
        fontsize=10,
        linespacing=1.35,
    )
    ax.set_ylabel("electrical power (kW)")
    peak = float(max(el_peak) if el_peak else 0)
    y_top = int(np.ceil((peak + 1.2) / 2)) * 2
    ax.set_ylim(0, max(y_top, 10))
    ax.grid(axis="y", alpha=0.3)
    ax.legend(loc="upper right")

    worst_i = int(np.argmax(el_peak))
    info = (f"Peak electrical draw: {el_peak[worst_i]:.2f} kW\n"
            f"  ({month_labels[worst_i]} @ {r['outdoor'][peak_idx[worst_i]]:.1f} °C, "
            f"COP {cop_peak[worst_i]:.2f})")
    ax.text(0.015, 0.97, info, transform=ax.transAxes, ha="left", va="top",
            fontsize=9, family="monospace",
            bbox=dict(boxstyle="round", fc="white", ec="#cf222e", alpha=0.9))
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    out = house_output_dir() / "sim_worst_case.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def main() -> None:
    scenario_name = sys.argv[1] if len(sys.argv) > 1 else "full_year"
    house_output_dir().mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    delta_t = float(cfg["operation"]["delta_t_k"])
    price = float(cfg.get("cost", {}).get("electricity_price_eur_per_kwh", 0.25))
    hp = FittedHeatPump(HeatPumpSpec.from_config(cfg), delta_t_k=delta_t)
    house_cfg = load_house_config()
    house = House.from_config(house_cfg)
    heating_curve = HeatingCurve.from_config(cfg, house_cfg)
    house_label = Path(os.environ.get("HOUSE_CONFIG", "house_config.toml")).name
    drv = WeatherDriver.from_config(cfg, house_cfg)
    print(f"Weather              : {drv.source_label}")

    if scenario_name == "worst_case":
        print(f"Weather (worst case) : {drv.worst_case_source_label}")
        out = run_worst_case(
            house, hp, drv.worst_case_year(), heating_curve,
            house_label, drv.worst_case_title_label, drv.worst_case_manifest(),
        )
    else:
        full = drv.full_year()
        dhw = compute_dhw(house_cfg, hp, full.data)
        out = run_full_year(house, hp, full, heating_curve, price, house_label, dhw,
                            drv.title_label)

    print(f"Simulation plot     : {out}")


if __name__ == "__main__":
    main()
