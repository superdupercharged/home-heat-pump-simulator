"""Radiator adequacy check.

For each room, compare the radiator's heat output at a given flow temperature
against the room's design heat loss (at the design outdoor temperature). Rooms
where the radiator can't cover the load would stay too cold and need either a
higher flow temperature, a bigger radiator, or backup heat.

Usage:
    .venv/bin/python radiator_check.py
    HOUSE_CONFIG=house_config_rehgraeble.toml .venv/bin/python radiator_check.py
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

    print(f"RADIATOR ADEQUACY CHECK  |  house: {house_label}")
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
    bars = ax.bar(range(len(names)), cov, color=colors)
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


if __name__ == "__main__":
    main()
