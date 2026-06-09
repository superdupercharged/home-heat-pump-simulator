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
a monthly table and plots in output/.
"""

from __future__ import annotations

import calendar
import sys
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from home_heat_sim import FittedHeatPump, HeatPumpSpec, load_config  # noqa: E402
from house_model import House, load_house_config  # noqa: E402
from weather import WeatherDriver  # noqa: E402

OUTPUT_DIR = Path(__file__).with_name("output")


def couple(house: House, hp: FittedHeatPump, scenario, flow_temp: float) -> dict:
    """Run the coupled house + heat pump model over a weather scenario."""
    outdoor = scenario.data["t_out"].to_numpy()
    active = house.heating_active(scenario.data["time"])
    demand = house.power_series(outdoor, dt_hours=scenario.dt_hours,
                                use_inertia=scenario.use_inertia,
                                active=active)["total_w"]

    hpres = hp.simulate_series(outdoor, flow_temp)
    cop = hpres["COP"]
    capacity = hpres["P_th"]

    delivered_hp = np.minimum(demand, capacity)
    p_el_hp = np.divide(delivered_hp, cop, out=np.zeros_like(demand), where=cop > 0)
    backup_th = np.clip(demand - capacity, 0.0, None)   # resistive, COP = 1
    p_el_total = p_el_hp + backup_th

    return {"outdoor": outdoor, "demand_w": demand, "cop": cop,
            "capacity_w": capacity, "p_el_hp_w": p_el_hp,
            "backup_w": backup_th, "p_el_total_w": p_el_total}


def run_full_year(house, hp, scenario, flow_temp, price) -> Path:
    df = scenario.data
    r = couple(house, hp, scenario, flow_temp)
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

    print(f"COUPLED HOUSE + HEAT PUMP - FULL YEAR (flow {flow_temp:.0f} °C)\n")
    print(f"  Heat delivered      : {th_kwh:,.0f} kWh")
    print(f"  JAZ / year-COP      : {scop:.2f}")
    print(f"  Heat pump elec.     : {hp_el_kwh:,.0f} kWh")
    print(f"  Backup elec.        : {backup_kwh:,.0f} kWh")
    print(f"  Total electricity   : {el_kwh:,.0f} kWh")
    print(f"  Peak electrical draw: {peak_el:.2f} kW")
    print(f"  Backup heater       : {backup_kwh / th_kwh * 100:.1f}% of heat, "
          f"{backup_hours} h/year")
    print(f"  Electricity bill     : {yearly_cost:,.0f} EUR/year "
          f"(~{monthly_cost:,.0f} EUR/month avg) @ {price:.2f} EUR/kWh\n")

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

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 8))
    ax1.fill_between(range(n_days), th_day, color="#ffd8a8", label="heat delivered")
    ax1.plot(range(n_days), el_day, color="#1f6feb", lw=1.2, label="electricity used")
    ax1.set_title(f"Daily energy: heat vs electricity (flow {flow_temp:.0f} °C, "
                  f"SCOP {scop:.2f})")
    ax1.set_xlabel("day of year")
    ax1.set_ylabel("energy (kWh/day)")
    ax1.set_xlim(0, n_days - 1)
    ax1.grid(alpha=0.3)
    ax1.legend(loc="upper center")

    info = (f"JAZ (year-COP): {scop:.2f}\n"
            f"Heat delivered: {th_kwh:,.0f} kWh\n"
            f"Heat pump elec.: {hp_el_kwh:,.0f} kWh\n"
            f"Backup elec.: {backup_kwh:,.0f} kWh\n"
            f"Total elec.: {el_kwh:,.0f} kWh\n"
            f"Peak draw: {peak_el:.1f} kW\n"
            f"Bill @ {price:.2f} EUR/kWh:\n"
            f"  {yearly_cost:,.0f} EUR/yr ({monthly_cost:,.0f} EUR/mo avg)")
    ax1.text(0.5, 0.5, info, transform=ax1.transAxes, ha="center", va="center",
             fontsize=9, family="monospace",
             bbox=dict(boxstyle="round", fc="white", ec="#1f6feb", alpha=0.9))

    heating = r["demand_w"] > 1
    sys_cop = np.divide(r["demand_w"], r["p_el_total_w"],
                        out=np.zeros_like(r["demand_w"]), where=r["p_el_total_w"] > 0)
    sc = ax2.scatter(r["outdoor"][heating], sys_cop[heating], s=6,
                     c=r["backup_w"][heating] > 1, cmap="coolwarm", alpha=0.5)
    ax2.set_title("Effective system COP vs outdoor temperature "
                  "(red = backup heater active)")
    ax2.set_xlabel("outdoor temperature (°C)")
    ax2.set_ylabel("system COP")
    ax2.grid(alpha=0.3)
    ax2.axhline(1.0, color="#cf222e", ls="--", lw=0.8)

    fig.tight_layout()
    out = OUTPUT_DIR / "sim_full_year.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def run_worst_case(house, hp, scenario, flow_temp) -> Path:
    df = scenario.data
    r = couple(house, hp, scenario, flow_temp)

    print(f"COUPLED HOUSE + HEAT PUMP - WORST CASE PER MONTH (flow {flow_temp:.0f} °C)\n")
    print(f"  {'Month':<6}{'T_out':>7}{'Demand':>9}{'COP':>6}"
          f"{'Capacity':>10}{'Elec':>8}{'Backup':>8}")
    for i, (_, row) in enumerate(df.iterrows()):
        m = int(row["month"])
        print(f"  {calendar.month_abbr[m]:<6}{r['outdoor'][i]:>6.1f}°C"
              f"{r['demand_w'][i] / 1000:>7.2f}kW{r['cop'][i]:>6.2f}"
              f"{r['capacity_w'][i] / 1000:>8.2f}kW"
              f"{r['p_el_total_w'][i] / 1000:>6.2f}kW"
              f"{r['backup_w'][i] / 1000:>6.2f}kW")

    months = [calendar.month_abbr[int(m)] for m in df["month"]]
    el_kw = r["p_el_total_w"] / 1000
    hp_kw = r["p_el_hp_w"] / 1000
    bk_kw = r["backup_w"] / 1000

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(months, hp_kw, color="#1f6feb", label="heat pump electricity")
    ax.bar(months, bk_kw, bottom=hp_kw, color="#cf222e", label="backup heater")
    for i, v in enumerate(el_kw):
        ax.text(i, v + 0.05, f"{r['cop'][i]:.1f}", ha="center", va="bottom",
                fontsize=8, color="#444")
    ax.set_title(f"Worst-case electrical draw per month (flow {flow_temp:.0f} °C, "
                 f"COP labeled)")
    ax.set_ylabel("electrical power (kW)")
    ax.grid(axis="y", alpha=0.3)
    ax.legend(loc="upper right")

    worst_i = int(el_kw.argmax())
    info = (f"Peak electrical draw: {el_kw[worst_i]:.2f} kW\n"
            f"  ({months[worst_i]} @ {r['outdoor'][worst_i]:.1f} °C, "
            f"COP {r['cop'][worst_i]:.2f})")
    ax.text(0.015, 0.97, info, transform=ax.transAxes, ha="left", va="top",
            fontsize=9, family="monospace",
            bbox=dict(boxstyle="round", fc="white", ec="#cf222e", alpha=0.9))
    fig.tight_layout()
    out = OUTPUT_DIR / "sim_worst_case.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def main() -> None:
    scenario_name = sys.argv[1] if len(sys.argv) > 1 else "full_year"
    OUTPUT_DIR.mkdir(exist_ok=True)

    cfg = load_config()
    flow_temp = float(cfg["operation"]["flow_temp_c"])
    delta_t = float(cfg["operation"]["delta_t_k"])
    price = float(cfg.get("cost", {}).get("electricity_price_eur_per_kwh", 0.25))
    hp = FittedHeatPump(HeatPumpSpec.from_config(cfg), delta_t_k=delta_t)
    house = House.from_config(load_house_config())
    drv = WeatherDriver()

    if scenario_name == "worst_case":
        out = run_worst_case(house, hp, drv.worst_case_per_month(), flow_temp)
    else:
        out = run_full_year(house, hp, drv.full_year(), flow_temp, price)

    print(f"\nPlot saved to: {out}")


if __name__ == "__main__":
    main()
