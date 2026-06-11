"""House thermal model for the heat pump simulator.

Steady-state per-room UA*dT heat loss, split over two heated levels:
  - ground floor loses heat through the floor into the (unheated) basement
  - first floor  loses heat through the ceiling into the (unheated) attic

Basement and attic temperatures are not fixed: they follow a time-lagged
outdoor temperature via the BufferModel (linear interpolation + exponential
smoothing for thermal inertia).

If ``[building]`` gives a footprint, each heated level also gets an automatic
* circulation proxy* room for unmodeled area:

    net floor  = footprint × (1 − 10 % walls)
    proxy area = net floor − sum(configured room floor areas)

The proxy has floor/ceiling loss into basement/attic and infiltration, but no
exterior walls or radiators.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Default fraction of gross floor area taken up by walls/partitions, used when
# [building] wall_area_fraction is not set. A rough wall take-off (30 cm outer,
# 20 cm inner) lands around 0.12-0.13 for a typical house.
DEFAULT_WALL_AREA_FRACTION = 0.12
CIRCULATION_PROXY_MIN_AREA_M2 = 0.5
CIRCULATION_PROXY_ROOM_TEMP_C = 20.0

CONFIG_DIR = Path(__file__).resolve().parent / "config"
HOUSE_CONFIG_PATH = CONFIG_DIR / "house_config.toml"

# Volumetric heat capacity of air ≈ 0.34 Wh/(m³·K) (1.2 kg/m³ * 1005 J/(kg·K)).
VOL_HEAT_CAP_AIR_WH = 0.34


def _resolve_house_config_path(path: Path) -> Path:
    """Resolve a house config path (env var may be a bare filename)."""
    if path.is_file():
        return path
    candidate = CONFIG_DIR / path.name
    if candidate.is_file():
        return candidate
    return path


def load_house_config(path: Path | None = None) -> dict:
    """Load a house config. Defaults to config/house_config.toml, but can be
    overridden via the HOUSE_CONFIG environment variable or an explicit path.
    """
    if path is None:
        env = os.environ.get("HOUSE_CONFIG")
        path = Path(env) if env else HOUSE_CONFIG_PATH
    path = _resolve_house_config_path(path)
    with open(path, "rb") as fh:
        return tomllib.load(fh)


@dataclass
class BufferModel:
    """Temperature of an unheated buffer space (basement / attic).

    The buffer temperature is a linear interpolation between two anchor points
    against a *time-lagged* outdoor temperature. The lag (thermal inertia) is an
    exponential moving average with time constant ``inertia_hours``.
    """

    outdoor_high_c: float
    buffer_high_c: float
    outdoor_low_c: float
    buffer_low_c: float
    inertia_hours: float

    @classmethod
    def from_config(cls, d: dict) -> "BufferModel":
        return cls(
            outdoor_high_c=float(d["outdoor_high_c"]),
            buffer_high_c=float(d["buffer_high_c"]),
            outdoor_low_c=float(d["outdoor_low_c"]),
            buffer_low_c=float(d["buffer_low_c"]),
            inertia_hours=float(d["inertia_hours"]),
        )

    def _map(self, t_out):
        # np.interp clamps to the endpoints outside [outdoor_low, outdoor_high].
        return np.interp(
            t_out,
            [self.outdoor_low_c, self.outdoor_high_c],
            [self.buffer_low_c, self.buffer_high_c],
        )

    def steady_temp(self, outdoor_c: float) -> float:
        """Buffer temperature for a steady outdoor temperature (no lag)."""
        return float(self._map(outdoor_c))

    def smooth_outdoor(self, outdoor_series, dt_hours: float = 1.0) -> np.ndarray:
        """Exponentially smoothed (lagged) outdoor temperature series."""
        outdoor = np.asarray(outdoor_series, dtype=float)
        if self.inertia_hours <= 0:
            return outdoor.copy()
        alpha = 1.0 - np.exp(-dt_hours / self.inertia_hours)
        smoothed = np.empty_like(outdoor)
        state = outdoor[0]
        for i, x in enumerate(outdoor):
            state += alpha * (x - state)
            smoothed[i] = state
        return smoothed

    def series_temp(self, outdoor_series, dt_hours: float = 1.0) -> np.ndarray:
        """Buffer temperature for an outdoor temperature time series (with lag)."""
        return self._map(self.smooth_outdoor(outdoor_series, dt_hours))


@dataclass
class Room:
    name: str
    floor_area_m2: float
    room_temp_c: float
    exterior_wall_area_m2: float
    window_area_m2: float
    heater_nominal_power_w: float
    heater_exponent: float
    # Either the EN 442 rating ΔT (75/65/20 -> 50 K) or, preferred, the flow
    # temperature the rated power refers to. Both are optional.
    heater_rating_delta_t_k: float | None = None
    heater_rating_flow_c: float | None = None
    # Daily window-airing time (Stoßlüften) in minutes; drives ventilation loss.
    airing_minutes_per_day: float = 0.0
    # Interior-only space (no exterior envelope) -> no baseline infiltration.
    interior_only: bool = False

    @classmethod
    def from_config(cls, d: dict) -> "Room":
        rating_dt = d.get("heater_rating_delta_t_k")
        rating_flow = d.get("heater_rating_flow_c")
        return cls(
            name=d["name"],
            floor_area_m2=float(d["floor_area_m2"]),
            room_temp_c=float(d["room_temp_c"]),
            exterior_wall_area_m2=float(d["exterior_wall_area_m2"]),
            window_area_m2=float(d["window_area_m2"]),
            heater_nominal_power_w=float(d["heater_nominal_power_w"]),
            heater_exponent=float(d["heater_exponent"]),
            heater_rating_delta_t_k=None if rating_dt is None else float(rating_dt),
            heater_rating_flow_c=None if rating_flow is None else float(rating_flow),
            airing_minutes_per_day=float(d.get("airing_minutes_per_day", 0.0)),
        )

    @classmethod
    def circulation_proxy(cls, floor_label: str, area_m2: float) -> "Room":
        """Synthetic room for unmodeled circulation area on a heated level."""
        return cls(
            name=f"Verkehrsfläche (Proxy, {floor_label})",
            floor_area_m2=area_m2,
            room_temp_c=CIRCULATION_PROXY_ROOM_TEMP_C,
            exterior_wall_area_m2=0.0,
            window_area_m2=0.0,
            heater_nominal_power_w=0.0,
            heater_exponent=1.30,
            interior_only=True,
        )

    def airing_ua(self, ceiling_height_m: float, ach_open: float) -> float:
        """Window-airing (Stoßlüften) heat-loss coefficient [W/K].

        Only rooms with a configured ``airing_minutes_per_day`` contribute:
        ``ach_open`` while the window is open, averaged over the day by the open
        fraction (airing_minutes / (60*24)). Energy scales with (T_room - T_out).
        """
        if self.airing_minutes_per_day <= 0:
            return 0.0
        volume = self.floor_area_m2 * ceiling_height_m
        ach_avg = ach_open * (self.airing_minutes_per_day / 60.0) / 24.0
        return VOL_HEAT_CAP_AIR_WH * volume * ach_avg

    def infiltration_ua(self, ceiling_height_m: float,
                        baseline_ach: float = 0.0) -> float:
        """Baseline infiltration heat-loss coefficient [W/K].

        Continuous (24/7) leakage through the building envelope (undichte Hülle).
        Interior-only spaces (e.g. circulation proxies) have no exterior envelope,
        so they get no infiltration. Energy scales with (T_room - T_out).
        """
        if self.interior_only:
            return 0.0
        volume = self.floor_area_m2 * ceiling_height_m
        return VOL_HEAT_CAP_AIR_WH * volume * baseline_ach

    def radiator_output_w(self, flow_temp_c: float,
                          delta_t_spread_k: float = 5.0,
                          std_room_c: float = 20.0) -> float:
        """Radiator heat output [W] at a given flow temperature.

        Output scales with the temperature difference to the power of the
        radiator exponent:  P = P_rated * (dT_actual / dT_rated)^n, where
        dT is the mean water temperature minus the room temperature.

        The rated power is interpreted via heater_rating_flow_c (preferred, rated
        against a standard 20 °C room) or heater_rating_delta_t_k (EN 442 ΔT).
        """
        mean_water = flow_temp_c - delta_t_spread_k / 2.0
        dt_actual = mean_water - self.room_temp_c
        if dt_actual <= 0:
            return 0.0
        if self.heater_rating_flow_c is not None:
            dt_rated = (self.heater_rating_flow_c - delta_t_spread_k / 2.0) - std_room_c
        elif self.heater_rating_delta_t_k is not None:
            dt_rated = self.heater_rating_delta_t_k
        else:
            dt_rated = 50.0
        return self.heater_nominal_power_w * (dt_actual / dt_rated) ** self.heater_exponent

    def loss_w(self, envelope: dict, t_outside: float, t_buffer: float,
               horiz_u: float, ceiling_height_m: float = 0.0,
               ach_open: float = 0.0, baseline_ach: float = 0.0) -> dict:
        """Heat loss [W] split into components.

        Keys: wall, window, horiz (conduction), infiltration (leaky envelope,
        every room), airing (window Stoßlüften, only rooms with an airing time),
        and total. ``horiz_u`` is floor_u (ground floor -> basement) or ceiling_u
        (first floor -> attic); ``t_buffer`` is the corresponding buffer temp.
        """
        net_wall = max(self.exterior_wall_area_m2 - self.window_area_m2, 0.0)
        d_out = self.room_temp_c - t_outside
        d_buf = self.room_temp_c - t_buffer
        wall = net_wall * envelope["wall_u"] * d_out
        window = self.window_area_m2 * envelope["window_u"] * d_out
        horiz = self.floor_area_m2 * horiz_u * d_buf
        infiltration = self.infiltration_ua(ceiling_height_m, baseline_ach) * d_out
        airing = self.airing_ua(ceiling_height_m, ach_open) * d_out
        total = wall + window + horiz + infiltration + airing
        return {"wall": wall, "window": window, "horiz": horiz,
                "infiltration": infiltration, "airing": airing, "total": total}


@dataclass
class House:
    envelope: dict
    ground_floor: list[Room]
    first_floor: list[Room]
    basement: BufferModel
    attic: BufferModel
    design_outdoor_temp_c: float
    ceiling_height_m: float = 2.4
    vent_ach_open: float = 10.0
    baseline_ach: float = 0.0
    heating_season_start: str = "10-15"
    heating_season_end: str = "05-15"
    circulation_proxy_m2: dict[str, float] | None = None

    @staticmethod
    def _net_floor_area_m2(building: dict) -> float | None:
        """Net heated floor area per level from footprint minus wall share.

        The wall share is ``[building] wall_area_fraction`` (default 0.12).
        """
        length = building.get("footprint_length_m")
        width = building.get("footprint_width_m")
        if length is None or width is None:
            return None
        brutto = float(length) * float(width)
        wall_fraction = float(
            building.get("wall_area_fraction", DEFAULT_WALL_AREA_FRACTION)
        )
        return brutto * (1.0 - wall_fraction)

    @staticmethod
    def _with_circulation_proxies(
        rooms: list[Room], net_floor_area_m2: float, floor_label: str,
    ) -> tuple[list[Room], float]:
        """Append a proxy room if configured rooms do not cover the net floor area."""
        modeled = sum(r.floor_area_m2 for r in rooms)
        proxy_area = net_floor_area_m2 - modeled
        if proxy_area < CIRCULATION_PROXY_MIN_AREA_M2:
            return list(rooms), 0.0
        return [*rooms, Room.circulation_proxy(floor_label, proxy_area)], proxy_area

    @classmethod
    def from_config(cls, cfg: dict) -> "House":
        op = cfg.get("operation", {})
        vent = cfg.get("ventilation", {})
        building = cfg.get("building", {})
        net_area = cls._net_floor_area_m2(building)
        ground = [Room.from_config(r) for r in cfg["ground_floor"]["room"]]
        first = [Room.from_config(r) for r in cfg["first_floor"]["room"]]
        proxies: dict[str, float] = {}
        if net_area is not None:
            ground, ug_proxy = cls._with_circulation_proxies(ground, net_area, "UG")
            first, og_proxy = cls._with_circulation_proxies(first, net_area, "OG")
            if ug_proxy:
                proxies["UG"] = ug_proxy
            if og_proxy:
                proxies["OG"] = og_proxy
        return cls(
            envelope=cfg["envelope"],
            ground_floor=ground,
            first_floor=first,
            basement=BufferModel.from_config(cfg["buffer"]["basement"]),
            attic=BufferModel.from_config(cfg["buffer"]["attic"]),
            design_outdoor_temp_c=float(cfg["design"]["design_outdoor_temp_c"]),
            ceiling_height_m=float(building["ceiling_height_m"]),
            vent_ach_open=float(vent.get("air_changes_per_hour_open", 10.0)),
            baseline_ach=float(vent.get("air_changes_per_hour_baseline", 0.0)),
            heating_season_start=str(op.get("heating_season_start", "10-15")),
            heating_season_end=str(op.get("heating_season_end", "05-15")),
            circulation_proxy_m2=proxies or None,
        )

    def heating_active(self, times) -> np.ndarray:
        """Boolean mask: True where heating is on, given a datetime Series.

        The season wraps the new year (e.g. on mid-Oct .. off mid-May), so a
        day is active if it is on/after the start OR on/before the end date.
        """
        sm, sd = (int(x) for x in self.heating_season_start.split("-"))
        em, ed = (int(x) for x in self.heating_season_end.split("-"))
        key = times.dt.month.to_numpy() * 100 + times.dt.day.to_numpy()
        return (key >= sm * 100 + sd) | (key <= em * 100 + ed)

    def loss_coefficients(self) -> dict:
        """Aggregate UA coefficients so power can be vectorized over a series.

        Heating power decomposes linearly into streams, each A_* - B_* * T_ref
        with A_* = sum(UA * T_room) and B_* = sum(UA):
          envelope (walls+windows vs outdoor)
          infiltration (leaky envelope vs outdoor, every room)
          airing (window Stoßlüften vs outdoor, only rooms with an airing time)
          ground floor (floor vs basement) / first floor (ceiling vs attic)
        Infiltration air enters at outdoor temp, so it scales like the envelope;
        it is reported separately from airing ("Lüften").
        """
        wall_u = self.envelope["wall_u"]
        window_u = self.envelope["window_u"]
        floor_u = self.envelope["floor_u"]
        ceiling_u = self.envelope["ceiling_u"]

        a_env = b_env = a_flr = b_flr = a_clg = b_clg = 0.0
        a_inf = b_inf = a_air = b_air = 0.0
        for room in self.ground_floor + self.first_floor:
            net_wall = max(room.exterior_wall_area_m2 - room.window_area_m2, 0.0)
            ua_cond = net_wall * wall_u + room.window_area_m2 * window_u
            a_env += ua_cond * room.room_temp_c
            b_env += ua_cond
            ua_inf = room.infiltration_ua(self.ceiling_height_m, self.baseline_ach)
            a_inf += ua_inf * room.room_temp_c
            b_inf += ua_inf
            ua_air = room.airing_ua(self.ceiling_height_m, self.vent_ach_open)
            a_air += ua_air * room.room_temp_c
            b_air += ua_air
        for room in self.ground_floor:
            ua = room.floor_area_m2 * floor_u
            a_flr += ua * room.room_temp_c
            b_flr += ua
        for room in self.first_floor:
            ua = room.floor_area_m2 * ceiling_u
            a_clg += ua * room.room_temp_c
            b_clg += ua
        return {"a_env": a_env, "b_env": b_env, "a_flr": a_flr,
                "b_flr": b_flr, "a_clg": a_clg, "b_clg": b_clg,
                "a_inf": a_inf, "b_inf": b_inf, "a_air": a_air, "b_air": b_air}

    def power_series(self, outdoor, dt_hours: float = 1.0,
                     use_inertia: bool = True, active=None) -> dict:
        """Heating power [W] for an outdoor temperature series.

        Returns arrays for total power (clamped at >= 0; no cooling modeled),
        the three loss streams, and the basement/attic buffer temperatures.
        If ``active`` (a boolean mask) is given, demand is forced to 0 outside
        the heating season.
        """
        outdoor = np.asarray(outdoor, dtype=float)
        if use_inertia:
            t_base = self.basement.series_temp(outdoor, dt_hours)
            t_attic = self.attic.series_temp(outdoor, dt_hours)
        else:
            t_base = self.basement._map(outdoor)
            t_attic = self.attic._map(outdoor)

        c = self.loss_coefficients()
        envelope = c["a_env"] - c["b_env"] * outdoor   # walls + windows (conduction)
        infiltration = c["a_inf"] - c["b_inf"] * outdoor  # leaky envelope, all rooms
        airing = c["a_air"] - c["b_air"] * outdoor      # window Stoßlüften only
        floor = c["a_flr"] - c["b_flr"] * t_base
        ceiling = c["a_clg"] - c["b_clg"] * t_attic
        total = np.clip(envelope + infiltration + airing + floor + ceiling,
                        0.0, None)
        if active is not None:
            total = total * np.asarray(active, dtype=float)
        return {"total_w": total, "envelope_w": envelope,
                "infiltration_w": infiltration, "airing_w": airing,
                "floor_w": floor, "ceiling_w": ceiling,
                "t_basement": t_base, "t_attic": t_attic}

    def losses_at(self, t_outside: float, t_basement: float | None = None,
                  t_attic: float | None = None) -> dict:
        """Per-room and total losses [W] at a steady outdoor temperature."""
        if t_basement is None:
            t_basement = self.basement.steady_temp(t_outside)
        if t_attic is None:
            t_attic = self.attic.steady_temp(t_outside)

        rooms = []
        for room in self.ground_floor:
            br = room.loss_w(self.envelope, t_outside, t_basement,
                             self.envelope["floor_u"], self.ceiling_height_m,
                             self.vent_ach_open, self.baseline_ach)
            rooms.append(("GF", room, br))
        for room in self.first_floor:
            br = room.loss_w(self.envelope, t_outside, t_attic,
                             self.envelope["ceiling_u"], self.ceiling_height_m,
                             self.vent_ach_open, self.baseline_ach)
            rooms.append(("1F", room, br))

        total = sum(r[2]["total"] for r in rooms)
        return {
            "t_outside": t_outside,
            "t_basement": t_basement,
            "t_attic": t_attic,
            "rooms": rooms,
            "total_w": total,
        }


def main() -> None:
    house = House.from_config(load_house_config())
    t_design = house.design_outdoor_temp_c
    res = house.losses_at(t_design)

    print("Peak heat loss at design outdoor temperature")
    print(f"  outdoor = {t_design:.1f} °C | "
          f"basement = {res['t_basement']:.1f} °C | "
          f"attic = {res['t_attic']:.1f} °C\n")

    print(f"{'Lvl':>3} {'Room':<22} {'wall':>7} {'window':>7} "
          f"{'horiz':>7} {'total':>8}")
    for lvl, room, br in res["rooms"]:
        print(f"{lvl:>3} {room.name:<22} {br['wall']:>6.0f}W {br['window']:>6.0f}W "
              f"{br['horiz']:>6.0f}W {br['total']:>7.0f}W")

    gf = sum(b["total"] for l, _, b in res["rooms"] if l == "GF")
    ff = sum(b["total"] for l, _, b in res["rooms"] if l == "1F")
    horiz = sum(b["horiz"] for _, _, b in res["rooms"])
    print(f"\n  Ground floor: {gf/1000:.2f} kW   First floor: {ff/1000:.2f} kW")
    print(f"  Floor+ceiling (into basement/attic): {horiz/1000:.2f} kW "
          f"({horiz/res['total_w']*100:.0f}% of total)")
    print(f"  TOTAL peak heat loss: {res['total_w']/1000:.2f} kW")
    if house.circulation_proxy_m2:
        print("\n  Auto circulation proxies (net floor − modeled rooms):")
        for fl, area in sorted(house.circulation_proxy_m2.items()):
            print(f"    {fl}: {area:.1f} m²")

    print("\nBuffer temperature vs outdoor (steady, no lag):")
    print(f"  {'outdoor':>8}{'basement':>10}{'attic':>8}")
    for t in (18, 10, 5, 0, -5, -10, -12):
        print(f"  {t:>8.0f}{house.basement.steady_temp(t):>10.1f}"
              f"{house.attic.steady_temp(t):>8.1f}")


if __name__ == "__main__":
    main()
