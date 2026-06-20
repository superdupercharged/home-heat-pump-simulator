#!/usr/bin/env python3
"""Compare peak heating loads across house configs for heat-pump sizing.

Runs the coupled house + heat pump model for each ``house_config_*.toml`` and
plots peak thermal/electrical draw against the configured unit (from
``config/config.toml``) and an optional smaller nominal size (default 12 kW,
same COP curve scaled linearly).

Output: ``outputs/hp_sizing_comparison.png``

Usage:
    .venv/bin/python compare_sizing.py
    .venv/bin/python compare_sizing.py --alt-kw 10
    .venv/bin/python compare_sizing.py --houses rehgraeble,lukra
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from heating_curve import HeatingCurve  # noqa: E402
from home_heat_sim import FittedHeatPump, HeatPumpSpec, load_config  # noqa: E402
from house_model import CONFIG_DIR, OUTPUTS_ROOT, House, load_house_config  # noqa: E402
from run_simulation import couple  # noqa: E402
from weather import WeatherDriver  # noqa: E402

COMPARISON_PLOT = OUTPUTS_ROOT / "hp_sizing_comparison.png"


@dataclass
class ScenarioMetrics:
    label: str
    annual_heat_kwh: float
    peak_demand_kw: float
    peak_demand_t_out: float
    peak_demand_flow_c: float
    hp_cap_at_peak_kw: float
    headroom_at_peak_kw: float
    peak_elec_kw: float
    peak_elec_t_out: float
    design_demand_kw: float
    design_hp_cap_kw: float
    design_headroom_kw: float
    backup_kwh_16: float
    backup_hours_16: int
    backup_kwh_alt: float
    backup_hours_alt: int


def discover_house_configs(selected: list[str] | None = None) -> list[tuple[str, str]]:
    """Return (slug, filename) for each ``house_config_<slug>.toml``."""
    found: list[tuple[str, str]] = []
    for path in sorted(CONFIG_DIR.glob("house_config_*.toml")):
        slug = path.stem[len("house_config_"):]
        if selected and slug not in selected:
            continue
        found.append((slug, path.name))
    return found


def _backup_if_scaled(result: dict, scale: float, dt_hours: float) -> tuple[float, int]:
    demand = result["demand_w"]
    cap = result["capacity_w"] * scale
    backup = np.clip(demand - cap, 0.0, None)
    return float(backup.sum() * dt_hours / 1000), int((backup > 1).sum())


def analyze_house(
    slug: str,
    house_file: str,
    cfg: dict,
    hp: FittedHeatPump,
    alt_nominal_kw: float,
) -> dict[str, ScenarioMetrics]:
    os.environ["HOUSE_CONFIG"] = house_file
    house_cfg = load_house_config()
    house = House.from_config(house_cfg)
    curve = HeatingCurve.from_config(cfg, house_cfg)
    drv = WeatherDriver.from_config(cfg, house_cfg)
    scale_alt = alt_nominal_kw / hp.spec.nominal_p_th_kw

    t_design = curve.design_outdoor_temp_c
    flow_design = curve.flow_at_design_c
    hp_design = hp.operating_point(t_design, flow_design)
    q_design = house.power_series(np.array([t_design]), use_inertia=False)["total_w"][0] / 1000

    out: dict[str, ScenarioMetrics] = {}
    for key, scen_label, scenario in [
        ("full_year", "Full year", drv.full_year()),
        ("worst_case", "Worst case", drv.worst_case_year()),
    ]:
        r = couple(house, hp, scenario, curve)
        dt = scenario.dt_hours
        demand = r["demand_w"]
        cap = r["capacity_w"]
        p_el = r["p_el_total_w"]
        backup = r["backup_w"]
        outdoor = r["outdoor"]
        flow = r["flow_c"]

        i_d = int(np.argmax(demand))
        i_e = int(np.argmax(p_el))
        b_alt, h_alt = _backup_if_scaled(r, scale_alt, dt)

        out[key] = ScenarioMetrics(
            label=scen_label,
            annual_heat_kwh=float(demand.sum() * dt / 1000),
            peak_demand_kw=demand[i_d] / 1000,
            peak_demand_t_out=float(outdoor[i_d]),
            peak_demand_flow_c=float(flow[i_d]),
            hp_cap_at_peak_kw=cap[i_d] / 1000,
            headroom_at_peak_kw=(cap[i_d] - demand[i_d]) / 1000,
            peak_elec_kw=p_el[i_e] / 1000,
            peak_elec_t_out=float(outdoor[i_e]),
            design_demand_kw=q_design,
            design_hp_cap_kw=hp_design["P_th_kW"],
            design_headroom_kw=hp_design["P_th_kW"] - q_design,
            backup_kwh_16=float(backup.sum() * dt / 1000),
            backup_hours_16=int((backup > 1).sum()),
            backup_kwh_alt=b_alt,
            backup_hours_alt=h_alt,
        )
    return out


def _bar_group(ax, x, width, offsets, values, labels, colors):
    bars = []
    for off, vals, lab, col in zip(offsets, values, labels, colors):
        b = ax.bar(x + off, vals, width, label=lab, color=col, edgecolor="white", linewidth=0.6)
        bars.append(b)
    return bars


def plot_comparison(
    houses: list[str],
    metrics: dict[str, dict[str, ScenarioMetrics]],
    hp_name: str,
    hp_nominal_kw: float,
    alt_nominal_kw: float,
    weather_note: str,
    path: Path = COMPARISON_PLOT,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)

    fig = plt.figure(figsize=(13, 9))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.1, 1.0], hspace=0.38, wspace=0.28)

    ax_th = fig.add_subplot(gs[0, 0])
    ax_el = fig.add_subplot(gs[0, 1])
    ax_bu = fig.add_subplot(gs[1, 0])
    ax_tbl = fig.add_subplot(gs[1, 1])
    ax_tbl.axis("off")

    x = np.arange(len(houses))
    w = 0.22
    offsets = [-1.5 * w, -0.5 * w, 0.5 * w, 1.5 * w]
    colors = ["#0969da", "#cf222e", "#8250df", "#656d76"]

    def series(getter):
        fy = [getter(metrics[h]["full_year"]) for h in houses]
        wc = [getter(metrics[h]["worst_case"]) for h in houses]
        dp = [metrics[h]["full_year"].design_demand_kw for h in houses]
        return [fy, wc, dp]

    th_vals = series(lambda m: m.peak_demand_kw)
    _bar_group(ax_th, x, w, offsets, th_vals,
               ["Peak demand (2022)", "Peak demand (worst-case)", "Design load (−12 °C)"],
               colors[:3])
    ax_th.axhline(hp_nominal_kw, color="#1a7f37", ls="--", lw=1.4,
                  label=f"Configured HP {hp_nominal_kw:.0f} kW")
    ax_th.axhline(alt_nominal_kw, color="#bf8700", ls=":", lw=1.4,
                  label=f"Alt. nominal {alt_nominal_kw:.0f} kW (scaled)")
    ax_th.set_xticks(x, houses)
    ax_th.set_ylabel("Thermal power (kW)")
    ax_th.set_title("Peak heat demand vs heat-pump size")
    ax_th.grid(axis="y", alpha=0.25)
    ax_th.legend(fontsize=7, loc="upper left")

    el_vals = [
        [metrics[h]["full_year"].peak_elec_kw for h in houses],
        [metrics[h]["worst_case"].peak_elec_kw for h in houses],
    ]
    _bar_group(ax_el, x, w * 1.2, [-0.5 * w, 0.5 * w], el_vals,
               ["Peak elec. (2022)", "Peak elec. (worst-case)"],
               ["#0550ae", "#a40e26"])
    ax_el.set_xticks(x, houses)
    ax_el.set_ylabel("Electrical power (kW)")
    ax_el.set_title("Peak electrical draw (configured HP)")
    ax_el.grid(axis="y", alpha=0.25)
    ax_el.legend(fontsize=7)

    w2 = 0.18
    for i, house in enumerate(houses):
        fy16 = metrics[house]["full_year"].backup_kwh_16
        wc16 = metrics[house]["worst_case"].backup_kwh_16
        fy_alt = metrics[house]["full_year"].backup_kwh_alt
        wc_alt = metrics[house]["worst_case"].backup_kwh_alt
        ax_bu.bar(i - 1.5 * w2, fy16, w2, color="#57606a", label="2022 / configured" if i == 0 else "")
        ax_bu.bar(i - 0.5 * w2, wc16, w2, color="#24292f", label="worst-case / configured" if i == 0 else "")
        ax_bu.bar(i + 0.5 * w2, fy_alt, w2, color="#bf8700", alpha=0.85,
                  label=f"2022 / {alt_nominal_kw:.0f} kW scaled" if i == 0 else "")
        ax_bu.bar(i + 1.5 * w2, wc_alt, w2, color="#953800", alpha=0.85,
                  label=f"worst-case / {alt_nominal_kw:.0f} kW scaled" if i == 0 else "")
    ax_bu.set_xticks(x, houses)
    ax_bu.set_ylabel("Backup heat (kWh / year)")
    ax_bu.set_title("Resistive backup energy (demand exceeding HP capacity)")
    ax_bu.grid(axis="y", alpha=0.25)
    ax_bu.legend(fontsize=7)

    rows = ["House", "Scenario", "Heat/yr", "Peak Q", "Peak Pel", "Headroom", "Backup 16kW", f"Backup {alt_nominal_kw:.0f}kW"]
    table_data = [rows]
    for house in houses:
        for key in ("full_year", "worst_case"):
            m = metrics[house][key]
            table_data.append([
                house,
                m.label,
                f"{m.annual_heat_kwh:.0f} kWh",
                f"{m.peak_demand_kw:.1f} kW @ {m.peak_demand_t_out:.0f}°C",
                f"{m.peak_elec_kw:.1f} kW",
                f"{m.headroom_at_peak_kw:+.1f} kW",
                f"{m.backup_kwh_16:.0f} kWh ({m.backup_hours_16} h)",
                f"{m.backup_kwh_alt:.0f} kWh ({m.backup_hours_alt} h)",
            ])

    tbl = ax_tbl.table(
        cellText=table_data[1:],
        colLabels=table_data[0],
        loc="center",
        cellLoc="left",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    tbl.scale(1.0, 1.35)
    for (row, col), cell in tbl.get_celld().items():
        if row == 0:
            cell.set_facecolor("#dbeafe")
            cell.set_text_props(weight="bold")
        elif row % 2 == 0:
            cell.set_facecolor("#f6f8fa")
    ax_tbl.set_title("Summary table", fontsize=10, pad=12)

    fig.suptitle(
        f"Heat pump sizing comparison  |  {hp_name}\n"
        f"Configured: {hp_nominal_kw:.0f} kW nominal  |  "
        f"Alt. comparison: {alt_nominal_kw:.0f} kW (linear scale, same COP curve)  |  {weather_note}",
        fontsize=11,
        y=0.98,
    )
    fig.subplots_adjust(left=0.07, right=0.97, top=0.88, bottom=0.08, hspace=0.42, wspace=0.32)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def print_summary(houses, metrics, hp_nominal_kw, alt_nominal_kw):
    print(f"Configured heat pump: {hp_nominal_kw:.0f} kW nominal")
    print(f"Alternative sizing:   {alt_nominal_kw:.0f} kW (scaled COP curve)\n")
    for house in houses:
        print(f"=== {house} ===")
        for key in ("full_year", "worst_case"):
            m = metrics[house][key]
            print(f"  {m.label}:")
            print(f"    Annual heat        {m.annual_heat_kwh:.0f} kWh")
            print(f"    Peak demand        {m.peak_demand_kw:.2f} kW @ {m.peak_demand_t_out:.1f}°C")
            print(f"    HP cap @ peak      {m.hp_cap_at_peak_kw:.2f} kW (headroom {m.headroom_at_peak_kw:+.2f} kW)")
            print(f"    Peak electrical    {m.peak_elec_kw:.2f} kW")
            print(f"    Design −12/55      {m.design_demand_kw:.2f} kW vs HP {m.design_hp_cap_kw:.2f} kW")
            print(f"    Backup {hp_nominal_kw:.0f} kW HP  {m.backup_kwh_16:.0f} kWh / {m.backup_hours_16} h")
            print(f"    Backup {alt_nominal_kw:.0f} kW HP  {m.backup_kwh_alt:.0f} kWh / {m.backup_hours_alt} h")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--alt-kw", type=float, default=12.0,
        help="Nominal kW of alternative unit for comparison (default: 12)",
    )
    parser.add_argument(
        "--houses", type=str, default="",
        help="Comma-separated slugs to compare (default: all house_config_*.toml)",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=COMPARISON_PLOT,
        help=f"Output PNG path (default: {COMPARISON_PLOT.relative_to(Path.cwd())})",
    )
    args = parser.parse_args()

    selected = [s.strip() for s in args.houses.split(",") if s.strip()] or None
    configs = discover_house_configs(selected)
    if not configs:
        print("No house configs found.", file=sys.stderr)
        sys.exit(1)

    cfg = load_config()
    hp = FittedHeatPump(
        HeatPumpSpec.from_config(cfg),
        delta_t_k=float(cfg["operation"]["delta_t_k"]),
    )
    hp_nominal = hp.spec.nominal_p_th_kw

    all_metrics: dict[str, dict[str, ScenarioMetrics]] = {}
    slugs = []
    for slug, house_file in configs:
        print(f"Analyzing {slug} ({house_file})…")
        all_metrics[slug] = analyze_house(slug, house_file, cfg, hp, args.alt_kw)
        slugs.append(slug)

    weather_note = f"weather year {cfg['weather'].get('year', '?')} (per-house location)"
    out = plot_comparison(
        slugs, all_metrics,
        hp_name=hp.spec.name,
        hp_nominal_kw=hp_nominal,
        alt_nominal_kw=args.alt_kw,
        weather_note=weather_note,
        path=args.output,
    )
    print_summary(slugs, all_metrics, hp_nominal, args.alt_kw)
    print(f"Comparison plot: {out}")


if __name__ == "__main__":
    main()
