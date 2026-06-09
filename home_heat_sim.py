"""Home heat pump simulator - core sim.

Builds a heat-pump model on top of `hplib` (https://github.com/RE-Lab-Projects/hplib)
whose performance curve is *fitted* to the manufacturer's datasheet points
(see config.toml / source_data/*.md), instead of using hplib's "Generic" curve.

hplib models an air/water heat pump as bilinear in ambient air and flow temp:

    COP  = c1 * t_amb + c2 * t_flow + c3
    P_el = P_el_ref * (e1 * t_amb + e2 * t_flow + e3)
    P_th = P_el * COP

We solve for the c* and e* coefficients with a least-squares fit over the
datasheet's rated operating points.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from hplib import hplib as hpl

CONFIG_PATH = Path(__file__).with_name("config.toml")

# Normalisation point for the electrical-power polynomial (A7 / W55).
_REF_T_AMB = 7.0
_REF_T_FLOW = 55.0


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


@dataclass
class HeatPumpSpec:
    """Datasheet description of a heat pump, read from config."""

    name: str
    group_id: int
    nominal_p_th_kw: float
    points: np.ndarray  # rows: [t_amb, t_flow, p_th_w, p_el_w, cop]

    @classmethod
    def from_config(cls, cfg: dict) -> "HeatPumpSpec":
        hp = cfg["heat_pump"]
        return cls(
            name=hp["name"],
            group_id=int(hp["group_id"]),
            nominal_p_th_kw=float(hp["nominal_p_th_kw"]),
            points=np.array(hp["datasheet"]["points"], dtype=float),
        )


class FittedHeatPump:
    """A heat pump whose hplib curve is fitted to datasheet rating points."""

    def __init__(self, spec: HeatPumpSpec, delta_t_k: float = 5.0):
        self.spec = spec
        t_amb = spec.points[:, 0]
        t_flow = spec.points[:, 1]
        p_el = spec.points[:, 3]
        cop = spec.points[:, 4]

        # Design matrix for the bilinear fit: [t_amb, t_flow, 1].
        design = np.column_stack([t_amb, t_flow, np.ones_like(t_amb)])

        c1, c2, c3 = np.linalg.lstsq(design, cop, rcond=None)[0]
        e_abs = np.linalg.lstsq(design, p_el, rcond=None)[0]  # absolute watts

        # Normalise P_el polynomial so it equals P_el_ref at the reference point.
        p_el_ref = float(e_abs @ [_REF_T_AMB, _REF_T_FLOW, 1.0])
        e1, e2, e3 = e_abs / p_el_ref
        cop_ref = float(c1 * _REF_T_AMB + c2 * _REF_T_FLOW + c3)

        parameters = pd.DataFrame(
            {
                "Group": [float(spec.group_id)],
                # COP polynomial (t_in == t_amb for air/water, so p4 folded into p1).
                "p1_COP [-]": [c1],
                "p2_COP [-]": [c2],
                "p3_COP [-]": [c3],
                "p4_COP [-]": [0.0],
                # Electrical-power polynomial (normalised to P_el_ref).
                "p1_P_el_h [1/°C]": [e1],
                "p2_P_el_h [1/°C]": [e2],
                "p3_P_el_h [-]": [e3],
                "p4_P_el_h [1/°C]": [0.0],
                "P_el_h_ref [W]": [p_el_ref],
                "P_th_h_ref [W]": [p_el_ref * cop_ref],
            }
        )
        self._model = hpl.HeatPump(parameters)
        self._model.delta_t = delta_t_k
        self.delta_t_k = delta_t_k
        self.cop_ref = cop_ref
        self.p_el_ref_kw = p_el_ref / 1000

    def operating_point(self, t_outside: float, t_flow: float) -> dict:
        """COP and powers (kW) at a given outdoor air and flow temperature."""
        res = self._model.simulate(
            t_in_primary=t_outside,
            t_in_secondary=t_flow - self.delta_t_k,
            t_amb=t_outside,
        )
        return {
            "t_outside": t_outside,
            "t_flow": t_flow,
            "COP": round(float(res["COP"]), 2),
            "P_el_kW": round(float(res["P_el"]) / 1000, 2),
            "P_th_kW": round(float(res["P_th"]) / 1000, 2),
        }

    def simulate_series(self, t_outside, t_flow: float) -> dict:
        """Full-load COP and powers for an outdoor temperature series.

        Returns numpy arrays (watts) for the heat pump running at full
        capacity: ``COP``, ``P_el`` (electrical input) and ``P_th`` (max
        thermal output / capacity) at each outdoor temperature.
        """
        t_outside = np.asarray(t_outside, dtype=float)
        res = self._model.simulate(
            t_in_primary=t_outside,
            t_in_secondary=np.full_like(t_outside, t_flow - self.delta_t_k),
            t_amb=t_outside,
        )
        return {"COP": np.asarray(res["COP"], dtype=float),
                "P_el": np.asarray(res["P_el"], dtype=float),
                "P_th": np.asarray(res["P_th"], dtype=float)}

    def fit_report(self) -> pd.DataFrame:
        """Compare fitted COP against the datasheet points (sanity check)."""
        rows = []
        for t_amb, t_flow, p_th, p_el, cop in self.spec.points:
            op = self.operating_point(t_amb, t_flow)
            rows.append(
                {
                    "A": t_amb,
                    "W": t_flow,
                    "COP_sheet": cop,
                    "COP_fit": op["COP"],
                    "P_el_sheet_kW": round(p_el / 1000, 2),
                    "P_el_fit_kW": op["P_el_kW"],
                }
            )
        return pd.DataFrame(rows)


def main() -> None:
    cfg = load_config()
    spec = HeatPumpSpec.from_config(cfg)
    delta_t = float(cfg["operation"]["delta_t_k"])
    flow_temp = float(cfg["operation"]["flow_temp_c"])

    hp = FittedHeatPump(spec, delta_t_k=delta_t)

    print(spec.name)
    print(f"Fit reference (A{_REF_T_AMB:.0f}/W{_REF_T_FLOW:.0f}): "
          f"COP_ref={hp.cop_ref:.2f}, P_el_ref={hp.p_el_ref_kw:.2f} kW\n")

    print("Fit vs datasheet:")
    print(hp.fit_report().to_string(index=False))

    print(f"\nConfigured flow temperature: {flow_temp:.0f} °C")
    print(f"{'T_out':>6} {'COP':>5} {'P_el':>8} {'P_th':>8}")
    for t_out in cfg["report"]["outdoor_temps_c"]:
        op = hp.operating_point(float(t_out), flow_temp)
        print(f"{op['t_outside']:>6.0f} {op['COP']:>5.2f} "
              f"{op['P_el_kW']:>6.2f}kW {op['P_th_kW']:>6.2f}kW")

    print("\nCOP matrix (rows = outdoor °C, cols = flow °C):")
    flows = [float(f) for f in cfg["report"]["flow_temps_c"]]
    header = "  T_out " + "".join(f"{f'W{f:.0f}':>7}" for f in flows)
    print(header)
    for t_out in cfg["report"]["outdoor_temps_c"]:
        cells = "".join(
            f"{hp.operating_point(float(t_out), f)['COP']:>7.2f}" for f in flows
        )
        print(f"{t_out:>6.0f} {cells}")


if __name__ == "__main__":
    main()
