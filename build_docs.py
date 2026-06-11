#!/usr/bin/env python3
"""Build the static GitHub Pages site in docs/.

Runs all simulations, copies plots to docs/assets/, and writes stats.json.

Usage:
    HOUSE_CONFIG=house_config_rehgraeble.toml .venv/bin/python build_docs.py
    cd docs && python3 -m http.server 8000   # preview at http://localhost:8000
"""

from __future__ import annotations

import calendar
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
ASSETS = DOCS / "assets"
OUTPUT = ROOT / "output"

PLOTS = [
    ("sim_full_year.png", "Full year: heat vs electricity"),
    ("sim_yearly_temps.png", "Yearly temperatures, demand & COP"),
    ("house_full_year.png", "House heat demand (no heat pump)"),
    ("radiator_check.png", "Radiator adequacy at design point"),
    ("radiator_room_energy.png", "Room energy split at design point"),
    ("sim_worst_case.png", "Worst-case electrical draw per month"),
    ("house_worst_case.png", "Worst-case house load per month"),
]

SCRIPTS = [
    ("run_simulation.py", ["full_year"]),
    ("run_simulation.py", ["worst_case"]),
    ("run_house.py", ["full_year"]),
    ("run_house.py", ["worst_case"]),
    ("room_check.py", []),
]


def run_simulations() -> None:
    py = sys.executable
    env = os.environ.copy()
    for script, args in SCRIPTS:
        cmd = [py, str(ROOT / script), *args]
        print(f"  {' '.join(cmd)}")
        subprocess.run(cmd, cwd=ROOT, env=env, check=True)


def collect_stats() -> dict:
    from heating_curve import HeatingCurve
    from home_heat_sim import FittedHeatPump, HeatPumpSpec, load_config
    from house_model import House, load_house_config
    from run_simulation import compute_dhw, couple
    from weather import WeatherDriver

    cfg = load_config()
    house_cfg = load_house_config()
    house = House.from_config(house_cfg)
    delta_t = float(cfg["operation"]["delta_t_k"])
    price = float(cfg.get("cost", {}).get("electricity_price_eur_per_kwh", 0.25))
    hp = FittedHeatPump(HeatPumpSpec.from_config(cfg), delta_t_k=delta_t)
    heating_curve = HeatingCurve.from_config(cfg, house_cfg)
    house_label = Path(os.environ.get("HOUSE_CONFIG", "house_config.toml")).name
    drv = WeatherDriver.from_config(cfg)
    scenario = drv.full_year()
    df = scenario.data
    r = couple(house, hp, scenario, heating_curve)
    dhw = compute_dhw(house_cfg, hp, df)
    dt = scenario.dt_hours

    th_kwh = float(r["demand_w"].sum() * dt / 1000)
    el_kwh = float(r["p_el_total_w"].sum() * dt / 1000)
    hp_el_kwh = float(r["p_el_hp_w"].sum() * dt / 1000)
    backup_kwh = float(r["backup_w"].sum() * dt / 1000)
    scop = th_kwh / el_kwh if el_kwh else None
    peak_el_kw = float(r["p_el_total_w"].max() / 1000)
    backup_hours = int((r["backup_w"] > 1).sum())

    house_th_kwh = th_kwh  # same demand series
    house_peak_kw = float(r["demand_w"].max() / 1000)

    dhw_heat = dhw["heat_kwh"] if dhw else 0.0
    dhw_el = dhw["elec_kwh"] if dhw else 0.0
    dhw_scop = dhw_heat / dhw_el if dhw_el else None
    total_el = el_kwh + dhw_el

    heating = r["demand_w"] > 1
    avg_flow = float(r["flow_c"][heating].mean()) if heating.any() else None

    months = df["month"].to_numpy()
    monthly = []
    for m in range(1, 13):
        mask = months == m
        if not mask.any():
            continue
        th = float(r["demand_w"][mask].sum() * dt / 1000)
        el = float(r["p_el_total_w"][mask].sum() * dt / 1000)
        monthly.append({
            "month": calendar.month_abbr[m],
            "heat_kwh": round(th),
            "elec_kwh": round(el),
            "cop": round(th / el, 2) if el else 0,
        })

    hc = cfg["heating_curve"]
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "house_config": house_label,
        "weather": drv.source_label,
        "weather_year": drv.weather_year,
        "heat_pump": cfg["heat_pump"]["name"],
        "heating_curve": heating_curve.label(),
        "heating_curve_params": {
            "flow_at_design_c": float(hc["flow_at_design_c"]),
            "flow_at_foot_c": float(hc["flow_at_foot_c"]),
            "design_outdoor_temp_c": float(hc["design_outdoor_temp_c"]),
            "foot_outdoor_temp_c": float(hc["foot_outdoor_temp_c"]),
            "level_offset_k": float(hc.get("level_offset_k", 0)),
            "outdoor_inertia_hours": float(hc.get("outdoor_inertia_hours", 24)),
            "heating_limit_c": float(hc.get("heating_limit_c", 15)),
        },
        "electricity_price_eur_per_kwh": price,
        "heating": {
            "heat_kwh": round(th_kwh),
            "elec_kwh": round(el_kwh),
            "scop": round(scop, 2) if scop else None,
            "hp_elec_kwh": round(hp_el_kwh),
            "backup_kwh": round(backup_kwh),
            "peak_el_kw": round(peak_el_kw, 2),
            "backup_hours": backup_hours,
            "avg_flow_c": round(avg_flow, 1) if avg_flow else None,
        },
        "dhw": {
            "heat_kwh": round(dhw_heat),
            "elec_kwh": round(dhw_el),
            "scop": round(dhw_scop, 2) if dhw_scop else None,
        } if dhw else None,
        "combined": {
            "elec_kwh": round(total_el),
            "cost_eur_per_year": round(total_el * price),
            "cost_eur_per_month": round(total_el * price / 12),
        },
        "house_demand": {
            "heat_kwh": round(house_th_kwh),
            "peak_kw": round(house_peak_kw, 2),
        },
        "monthly": monthly,
        "plots": [{"file": f, "title": t} for f, t in PLOTS],
    }


def copy_assets() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for filename, _ in PLOTS:
        src = OUTPUT / filename
        if not src.exists():
            raise FileNotFoundError(f"Missing plot: {src} — run simulations first")
        shutil.copy2(src, ASSETS / filename)
        print(f"  copied {filename}")


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    print("Running simulations…")
    run_simulations()
    print("Collecting stats…")
    stats = collect_stats()
    stats_path = DOCS / "stats.json"
    stats_path.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote {stats_path.relative_to(ROOT)}")
    print("Copying plots…")
    copy_assets()
    print(f"\nDone. Preview:\n  cd docs && python3 -m http.server 8000\n  → http://localhost:8000")


if __name__ == "__main__":
    main()
