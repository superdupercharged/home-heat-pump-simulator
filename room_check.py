"""Per-room checks at the design point.

Two analyses for the coldest design conditions (design outdoor temp, heating
curve's design flow temperature):

1. Radiator adequacy: per room, compare the radiator's heat output at the design
   flow temperature against the room's design heat loss. Rooms the radiator can't
   cover would stay too cold and need a higher flow temp, a bigger radiator, or
   backup heat.  ->  output/radiator_check.png
2. Room energy split: per room, the design heat loss split into transmission
   (Wärmeverlust), baseline infiltration (undichte Hülle) and window airing
   (Lüften), highest demand on top.  Includes the auto circulation-proxy rooms.
   ->  output/radiator_room_energy.png

Usage:
    .venv/bin/python room_check.py
    HOUSE_CONFIG=house_config_rehgraeble.toml .venv/bin/python room_check.py
"""

from __future__ import annotations

import os
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from heating_curve import HeatingCurve  # noqa: E402
from home_heat_sim import load_config  # noqa: E402
from house_model import House, load_house_config  # noqa: E402

OUTPUT_DIR = Path(__file__).with_name("output")


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    cfg = load_config()
    delta_t = float(cfg["operation"]["delta_t_k"])
    house_cfg = load_house_config()
    curve = HeatingCurve.from_config(cfg, house_cfg)
    flow_main = curve.flow_at_design_c
    flow_alt = flow_main + 3.0  # second comparison temperature (e.g. 55 -> 58)

    house = House.from_config(house_cfg)
    house_label = Path(os.environ.get("HOUSE_CONFIG", "house_config.toml")).name
    t_design = curve.design_outdoor_temp_c

    res = house.losses_at(t_design)

    print(f"ROOM CHECK  |  house: {house_label}")
    print(f"  {curve.label()}")
    print(f"  Check @ design point: {flow_main:.0f} / {flow_alt:.0f} °C Vorlauf, "
          f"spread {delta_t:.0f} K\n")
    header = (f"{'Room':<22}{'T_room':>7}{'Q_des':>8}"
              f"{'P@' + str(int(flow_main)):>8}{'cov':>6}"
              f"{'P@' + str(int(flow_alt)):>8}{'cov':>6}  status")
    print(header)
    print("-" * len(header))

    rows = []
    for lvl, room, br in res["rooms"]:
        q = br["total"]
        p_main = room.radiator_output_w(flow_main, delta_t)
        p_alt = room.radiator_output_w(flow_alt, delta_t)
        cov_main = p_main / q if q > 0 else float("inf")
        cov_alt = p_alt / q if q > 0 else float("inf")
        if q <= 0 or room.heater_nominal_power_w == 0:
            status = "n/a (no rad/load)" if room.heater_nominal_power_w == 0 else "ok"
        elif cov_main >= 1.0:
            status = "OK @ {:.0f}".format(flow_main)
        elif cov_alt >= 1.0:
            status = "needs {:.0f} °C".format(flow_alt)
        else:
            status = "UNDER even @ {:.0f}".format(flow_alt)
        rows.append((room.name, q, p_main, cov_main, p_alt, cov_alt, status))
        print(f"{room.name:<22}{room.room_temp_c:>6.0f}°{q:>7.0f}W"
              f"{p_main:>7.0f}W{cov_main * 100:>5.0f}%"
              f"{p_alt:>7.0f}W{cov_alt * 100:>5.0f}%  {status}")

    under_main = [r for r in rows if r[1] > 0 and r[2] > 0 and r[3] < 1.0]
    print(f"\n  Rooms underpowered at {flow_main:.0f} °C: "
          f"{len(under_main)} of {sum(1 for r in rows if r[2] > 0)}")
    if under_main:
        print("   " + ", ".join(f"{r[0]} ({r[3]*100:.0f}%)" for r in under_main))

    # --- Bar chart: coverage per room at the main flow temperature ---
    heated = [r for r in rows if r[2] > 0 and r[1] > 0]
    names = [r[0] for r in heated]
    cov = [r[3] * 100 for r in heated]
    colors = ["#2da44e" if c >= 100 else ("#d29922" if c >= 85 else "#cf222e")
              for c in cov]

    fig, ax = plt.subplots(figsize=(11, 6))
    ax.bar(range(len(names)), cov, color=colors)
    ax.axhline(100, color="#444", ls="--", lw=1, label="100 % (deckt Last)")
    for i, c in enumerate(cov):
        ax.text(i, c + 1, f"{c:.0f}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=40, ha="right", fontsize=8)
    ax.set_ylabel("Heizkörper-Deckung (%)")
    ax.set_title(f"Heizkörper-Adäquanz @ {flow_main:.0f} °C Vorlauf  |  "
                 f"house: {house_label}  |  Auslegung {t_design:.0f} °C")
    ax.grid(axis="y", alpha=0.3)
    ax.legend(loc="upper right")
    fig.tight_layout()
    out = OUTPUT_DIR / "radiator_check.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    print(f"\nPlot saved to: {out}")

    out2 = plot_room_energy_split(res, house_label, t_design)
    print(f"Plot saved to: {out2}")


def plot_room_energy_split(res: dict, house_label: str, t_design: float) -> Path:
    """Horizontal stacked bars: per-room design heat loss split into
    transmission (Wärmeverlust) and ventilation (Lüften), highest on top."""
    rooms = []
    for _lvl, room, br in res["rooms"]:
        # Three segments: conduction transmission, baseline infiltration (undichte
        # Hülle, every room), and window airing (only rooms with an airing time).
        trans = br["wall"] + br["window"] + br["horiz"]
        infil = br["infiltration"]
        airing = br["airing"]
        total = trans + infil + airing
        if total <= 0:
            continue
        rooms.append((room.name, trans, infil, airing, total))
    rooms.sort(key=lambda r: r[4])  # ascending; invert_yaxis puts max on top

    names = [r[0] for r in rooms]
    trans = np.array([r[1] for r in rooms])
    infil = np.array([r[2] for r in rooms])
    airing = np.array([r[3] for r in rooms])
    totals = np.array([r[4] for r in rooms])
    y = np.arange(len(names))

    fig, ax = plt.subplots(figsize=(11, max(5, 0.5 * len(names) + 1)))
    ax.barh(y, trans, color="#d29922", label="Transmission loss")
    ax.barh(y, infil, left=trans, color="#8957e5",
            label="Infiltration (leaky envelope)")
    ax.barh(y, airing, left=trans + infil, color="#1f6feb",
            label="Airing (windows)")
    for i, tot in enumerate(totals):
        ax.text(tot + totals.max() * 0.01, i, f"{tot:.0f} W",
                va="center", ha="left", fontsize=8)
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(0, totals.max() * 1.12)
    ax.set_xlabel("Design heat load (W) @ {:.0f} °C outdoor".format(t_design))
    ax.set_title(f"Energy split per room  |  house: {house_label}  |  "
                 f"design {t_design:.0f} °C")
    ax.grid(axis="x", alpha=0.3)
    ax.legend(loc="upper right")
    fig.tight_layout()
    out = OUTPUT_DIR / "radiator_room_energy.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


if __name__ == "__main__":
    main()
