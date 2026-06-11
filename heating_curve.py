"""Weather-compensated heating curve (Heizkurve).

Maps outdoor temperature to supply (flow) water temperature using a linear
curve between a mild-weather foot point and the design point at norm outdoor
temperature (NAT, typically from DIN EN 12831).

Real heat-pump controllers use a *damped* outdoor temperature (gedämpfte
Außentemperatur) as the curve input so the flow setpoint cannot jump hourly.

Formula (two-point linear interpolation):

    T_flow = T_flow_foot + (T_out - T_out_foot) * (T_flow_design - T_flow_foot)
                              / (T_out_design - T_out_foot)

Plus an optional level offset (Niveau / parallel shift).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
OUTPUT_DIR = Path(__file__).with_name("output")


def _ema(series: np.ndarray, inertia_hours: float, dt_hours: float) -> np.ndarray:
    """Exponential moving average (first-order lag)."""
    if inertia_hours <= 0:
        return series.copy()
    alpha = 1.0 - np.exp(-dt_hours / inertia_hours)
    out = np.empty_like(series)
    state = float(series[0])
    for i, x in enumerate(series):
        state += alpha * (float(x) - state)
        out[i] = state
    return out


@dataclass
class HeatingCurve:
    """Linear heating curve: flow temp vs outdoor temp."""

    room_temp_c: float
    design_outdoor_temp_c: float
    flow_at_design_c: float
    foot_outdoor_temp_c: float
    flow_at_foot_c: float
    heating_limit_c: float
    level_offset_k: float
    min_flow_c: float
    max_flow_c: float
    outdoor_inertia_hours: float

    @classmethod
    def from_config(cls, cfg: dict, house_cfg: dict | None = None) -> "HeatingCurve":
        hc = cfg["heating_curve"]
        house_cfg = house_cfg or {}
        design_out = float(
            hc.get(
                "design_outdoor_temp_c",
                house_cfg.get("design", {}).get("design_outdoor_temp_c", -12.0),
            )
        )
        return cls(
            room_temp_c=float(hc.get("room_temp_c", 20.0)),
            design_outdoor_temp_c=design_out,
            flow_at_design_c=float(hc.get("flow_at_design_c", 55.0)),
            foot_outdoor_temp_c=float(hc.get("foot_outdoor_temp_c", 20.0)),
            flow_at_foot_c=float(hc.get("flow_at_foot_c", 25.0)),
            heating_limit_c=float(hc.get("heating_limit_c", 15.0)),
            level_offset_k=float(hc.get("level_offset_k", 0.0)),
            min_flow_c=float(hc.get("min_flow_c", 20.0)),
            max_flow_c=float(hc.get("max_flow_c", 65.0)),
            outdoor_inertia_hours=float(hc.get("outdoor_inertia_hours", 24.0)),
        )

    @property
    def slope(self) -> float:
        """Neigung: extra flow °C per 1 K drop in outdoor temp (positive = steeper)."""
        span = self.foot_outdoor_temp_c - self.design_outdoor_temp_c
        if abs(span) < 1e-6:
            return 0.0
        return (self.flow_at_design_c - self.flow_at_foot_c) / span

    def instant_flow_temp(self, t_outside) -> np.ndarray:
        """Flow setpoint from outdoor temp (no damping)."""
        t_out = np.asarray(t_outside, dtype=float)
        denom = self.design_outdoor_temp_c - self.foot_outdoor_temp_c
        if abs(denom) < 1e-6:
            raw = np.full_like(t_out, self.flow_at_design_c)
        else:
            raw = (
                self.flow_at_foot_c
                + (t_out - self.foot_outdoor_temp_c)
                * (self.flow_at_design_c - self.flow_at_foot_c)
                / denom
            )
        raw = raw + self.level_offset_k
        return np.clip(raw, self.min_flow_c, self.max_flow_c)

    def flow_temp_series(self, t_outside, dt_hours: float = 1.0) -> dict:
        """Hourly flow setpoints with optional outdoor-temp damping.

        Returns dict with keys:
          ``outdoor``       – raw outdoor series
          ``outdoor_damped``– lagged outdoor fed into the curve
          ``flow_target``   – instant curve output from damped outdoor
          ``flow``          – actual setpoint used (same as flow_target)
        """
        outdoor = np.asarray(t_outside, dtype=float)
        outdoor_damped = _ema(outdoor, self.outdoor_inertia_hours, dt_hours)
        flow = self.instant_flow_temp(outdoor_damped)
        return {
            "outdoor": outdoor,
            "outdoor_damped": outdoor_damped,
            "flow_target": flow,
            "flow": flow,
        }

    def label(self) -> str:
        lag = (f", gedämpft {self.outdoor_inertia_hours:.0f}h"
               if self.outdoor_inertia_hours > 0 else "")
        return (
            f"Heizkurve: {self.flow_at_foot_c:.0f}°C@{self.foot_outdoor_temp_c:.0f}°C"
            f" → {self.flow_at_design_c:.0f}°C@{self.design_outdoor_temp_c:.0f}°C"
            f", Neigung {self.slope:.2f}{lag}"
        )


def plot_heating_curve(curve: HeatingCurve, path: Path | None = None,
                       ax=None) -> Path | None:
    """Plot the configured heating curve. Saves to disk unless *ax* is given."""
    save = ax is None
    if save:
        path = path or OUTPUT_DIR / "heating_curve.png"
        path.parent.mkdir(exist_ok=True)
        fig, ax = plt.subplots(figsize=(8, 5))
    else:
        path = None

    t_out = np.linspace(-20.0, 25.0, 300)
    t_flow = curve.instant_flow_temp(t_out)

    ax.plot(t_out, t_flow, color="#1f6feb", lw=2.0 if save else 1.4,
            label="Heizkurve")

    ax.plot(
        curve.foot_outdoor_temp_c,
        curve.flow_at_foot_c + curve.level_offset_k,
        "o", color="#2da44e", ms=6 if save else 4, zorder=5,
    )
    ax.plot(
        curve.design_outdoor_temp_c,
        curve.flow_at_design_c + curve.level_offset_k,
        "o", color="#cf222e", ms=6 if save else 4, zorder=5,
    )
    ax.axvline(curve.heating_limit_c, color="#888", ls="--", lw=0.8)

    if save:
        info = (
            f"Neigung: {curve.slope:.2f}\n"
            f"Niveau: {curve.level_offset_k:+.1f} K\n"
            f"Gedämpfung: {curve.outdoor_inertia_hours:.0f} h\n"
            f"Min/Max VL: {curve.min_flow_c:.0f}–{curve.max_flow_c:.0f} °C"
        )
        ax.set_xlabel("Außentemperatur (°C)")
        ax.set_ylabel("Vorlauftemperatur (°C)")
        ax.set_title("Heizkurve (Vorlauf vs. Außentemperatur)")
        ax.grid(alpha=0.3)
        ax.legend(loc="upper right", fontsize=8)
        ax.text(
            0.02, 0.02, info, transform=ax.transAxes, ha="left", va="bottom",
            fontsize=9, family="monospace",
            bbox=dict(boxstyle="round", fc="white", ec="#1f6feb", alpha=0.9),
        )
        fig.tight_layout()
        fig.savefig(path, dpi=110)
        plt.close(fig)
    else:
        ax.set_xlabel("Außen °C", fontsize=7)
        ax.set_ylabel("VL °C", fontsize=7)
        ax.tick_params(labelsize=6)
        ax.grid(alpha=0.25)
        ax.set_title("Heizkurve", fontsize=8)

    return path


def plot_yearly_profile(curve: HeatingCurve, df, result: dict,
                        path: Path | None = None,
                        weather_label: str = "") -> Path:
    """Year timeline: outdoor T, flow T, COP; small heating-curve inset below."""
    path = path or OUTPUT_DIR / "sim_yearly_temps.png"
    path.parent.mkdir(exist_ok=True)

    day = df["day"].to_numpy()
    n_days = int(day.max()) + 1

    outdoor_day = np.array([result["outdoor"][day == d].mean() for d in range(n_days)])
    flow_day = np.array([result["flow_c"][day == d].mean() for d in range(n_days)])
    flow_instant_day = np.array([
        curve.instant_flow_temp(result["outdoor"][day == d]).mean()
        for d in range(n_days)
    ])
    heating = result["demand_w"] > 1
    cop_day = np.full(n_days, np.nan)
    cap_day_kw = np.full(n_days, np.nan)
    demand_day_kw = np.full(n_days, np.nan)
    demand_peak_kw = np.full(n_days, np.nan)
    for d in range(n_days):
        mask = (day == d) & heating
        if mask.any():
            cop_day[d] = result["cop"][mask].mean()
            cap_day_kw[d] = result["capacity_w"][mask].mean() / 1000
            demand_day_kw[d] = result["demand_w"][mask].mean() / 1000
            demand_peak_kw[d] = result["demand_w"][mask].max() / 1000

    month_starts = df.groupby("month")["day"].min()
    month_labels = [calendar.month_abbr[m] for m in range(1, 13)
                    if m in month_starts.index]
    month_ticks = [month_starts[m] for m in range(1, 13) if m in month_starts.index]
    days = np.arange(n_days)

    peak_cap = float(np.nanmax(cap_day_kw))
    peak_demand = float(np.nanmax(demand_peak_kw))

    fig = plt.figure(figsize=(13, 8))
    grid = fig.add_gridspec(3, 1, height_ratios=[2.2, 1.8, 0.9], hspace=0.22)
    ax_temp = fig.add_subplot(grid[0])
    ax_perf = fig.add_subplot(grid[1], sharex=ax_temp)
    ax_curve = fig.add_subplot(grid[2])

    # --- Top: temperatures only ---
    ax_temp.plot(days, outdoor_day, color="#888", lw=0.9, alpha=0.85,
                 label="Außentemp. (Tagesmittel)")
    if curve.outdoor_inertia_hours > 0:
        ax_temp.plot(days, flow_instant_day, color="#1f6feb", lw=0.7, ls=":",
                     alpha=0.45, label="Vorlauf ohne Dämpfung")
    ax_temp.plot(days, flow_day, color="#1f6feb", lw=1.3,
                 label="Vorlauf Soll (gedämpft, Tagesmittel)")
    ax_temp.set_ylabel("Temperatur (°C)")
    ax_temp.set_xlim(0, n_days - 1)
    ax_temp.grid(alpha=0.3)
    ax_temp.legend(loc="upper center", bbox_to_anchor=(0.55, 0.98), fontsize=9)
    weather_note = f"{weather_label}  |  " if weather_label else ""
    ax_temp.set_title(
        f"{weather_note}Temperaturen  |  {curve.label()}",
        fontsize=10,
    )
    plt.setp(ax_temp.get_xticklabels(), visible=False)

    # --- Middle: COP + heating power ---
    ax_perf.fill_between(days, 0, demand_day_kw, color="#2da44e", alpha=0.25)
    ax_perf.plot(days, demand_day_kw, color="#1a7f37", lw=1.4,
                 label="Bedarf (Tagesmittel)")
    ax_perf.plot(days, demand_peak_kw, color="#1a7f37", lw=0.9, ls=":",
                 alpha=0.7, label="Bedarf (Tagespeak)")
    ax_perf.plot(days, cap_day_kw, color="#cf222e", lw=1.2, ls="--",
                 label="WP-Kapazität (Tagesmittel)")
    ax_perf.set_ylabel("Leistung (kW)")
    ax_perf.set_ylim(0, max(18.0, peak_cap * 1.05))
    ax_perf.set_xticks(month_ticks)
    ax_perf.set_xticklabels(month_labels)
    ax_perf.grid(alpha=0.3)

    ax_cop = ax_perf.twinx()
    ax_cop.plot(days, cop_day, color="#d29922", lw=1.0, alpha=0.9,
                label="COP (Tagesmittel, Heizzeit)")
    ax_cop.set_ylabel("COP", color="#d29922")
    ax_cop.tick_params(axis="y", labelcolor="#d29922")
    ax_cop.set_ylim(0.8, max(4.5, np.nanmax(cop_day) + 0.3))

    lines1, labels1 = ax_perf.get_legend_handles_labels()
    lines2, labels2 = ax_cop.get_legend_handles_labels()
    ax_perf.legend(
        lines1 + lines2, labels1 + labels2,
        loc="upper center", bbox_to_anchor=(0.55, 0.98), fontsize=8,
    )
    ax_perf.set_title(
        f"COP & Leistung  |  Peak Bedarf {peak_demand:.1f} kW, "
        f"Peak Kapazität {peak_cap:.1f} kW  "
        f"(Kapazität ∝ Außen + Vorlauf)",
        fontsize=10,
    )

    # --- Bottom: heating curve reference ---
    plot_heating_curve(curve, ax=ax_curve)
    ax_curve.set_xlim(-20, 25)

    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


if __name__ == "__main__":
    from home_heat_sim import load_config
    from house_model import load_house_config

    cfg = load_config()
    curve = HeatingCurve.from_config(cfg, load_house_config())
    out = plot_heating_curve(curve)
    print(curve.label())
    print(f"Plot saved to: {out}")
