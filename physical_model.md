# Physical Model of Building Heat Loss and Domestic Hot-Water Energy

**Home Heat Pump Simulator — Technical Note**

---

## Abstract

This note documents the governing physical equations used by the simulator to
estimate the thermal energy demand of a dwelling. The model is a quasi-steady,
per-room *UA·ΔT* formulation evaluated hourly over a meteorological year. Three
loss mechanisms are treated: (i) **transmission** (conduction) through opaque
walls, windows, and the horizontal envelope (floor and ceiling) into adjacent
unheated buffer spaces; (ii) **ventilation**, split into continuous envelope
*infiltration* and intermittent window *airing* (*Stoßlüften*); and (iii)
**domestic hot water (DHW)**, derived from a measured monthly water-consumption
profile. All quantities are expressed per room and summed to the building level.
The implementation lives in `house_model.py` (transmission and ventilation) and
`water.py` (DHW).

---

## 1. Nomenclature

| Symbol | Description | Unit |
|---|---|---|
| $\dot{Q}$ | Heat-loss rate (power) | $\mathrm{W}$ |
| $U$ | Thermal transmittance (U-value) | $\mathrm{W\,m^{-2}\,K^{-1}}$ |
| $A$ | Surface area | $\mathrm{m^2}$ |
| $V$ | Room air volume | $\mathrm{m^3}$ |
| $h$ | Clear ceiling height | $\mathrm{m}$ |
| $T_\mathrm{room}$ | Room set-point temperature | $^\circ\mathrm{C}$ |
| $T_\mathrm{out}$ | Outdoor air temperature | $^\circ\mathrm{C}$ |
| $T_\mathrm{buf}$ | Buffer (basement / attic) temperature | $^\circ\mathrm{C}$ |
| $n$ | Air-change rate (ACH) | $\mathrm{h^{-1}}$ |
| $c_\mathrm{air}$ | Volumetric heat capacity of air | $\mathrm{Wh\,m^{-3}\,K^{-1}}$ |
| $c_\mathrm{w}$ | Volumetric heat capacity of water | $\mathrm{kWh\,m^{-3}\,K^{-1}}$ |
| $\mathrm{UA}$ | Lumped heat-loss coefficient | $\mathrm{W\,K^{-1}}$ |

### Physical constants

The volumetric heat capacity of air is

$$
c_\mathrm{air} = \rho_\mathrm{air}\, c_{p,\mathrm{air}}
= \frac{1.2\,\mathrm{kg\,m^{-3}} \times 1005\,\mathrm{J\,kg^{-1}\,K^{-1}}}{3600\,\mathrm{J\,Wh^{-1}}}
\approx 0.34\ \mathrm{Wh\,m^{-3}\,K^{-1}}.
\tag{1}
$$

The volumetric heat capacity of water is

$$
c_\mathrm{w} = \frac{\rho_\mathrm{w}\, c_{p,\mathrm{w}}}{3.6\times10^{6}}
= \frac{1000\,\mathrm{kg\,m^{-3}} \times 4186\,\mathrm{J\,kg^{-1}\,K^{-1}}}{3.6\times10^{6}\,\mathrm{J\,kWh^{-1}}}
\approx 1.163\ \mathrm{kWh\,m^{-3}\,K^{-1}}.
\tag{2}
$$

---

## 2. Transmission (conduction) losses

Each conductive path is modelled by the steady-state Fourier relation
$\dot{Q} = U A\,\Delta T$, where the driving temperature difference is taken
between the room and the medium on the far side of the surface.

### 2.1 Exterior walls

Window area is subtracted from the gross exterior wall area so that the same
surface is not counted twice:

$$
A_\mathrm{wall}^{\mathrm{net}} = \max\!\left(A_\mathrm{wall} - A_\mathrm{win},\ 0\right).
\tag{3}
$$

$$
\dot{Q}_\mathrm{wall} = A_\mathrm{wall}^{\mathrm{net}}\, U_\mathrm{wall}\,
\bigl(T_\mathrm{room} - T_\mathrm{out}\bigr).
\tag{4}
$$

### 2.2 Windows

$$
\dot{Q}_\mathrm{win} = A_\mathrm{win}\, U_\mathrm{win}\,
\bigl(T_\mathrm{room} - T_\mathrm{out}\bigr).
\tag{5}
$$

### 2.3 Floor and ceiling (horizontal envelope into buffer spaces)

Heat does not flow directly to outdoor air through the floor and ceiling.
Instead, a ground-floor room loses heat downward into the (unheated) basement
and a top-floor room loses heat upward into the (unheated) attic. The driving
difference therefore uses the buffer temperature $T_\mathrm{buf}$:

$$
\dot{Q}_\mathrm{floor} = A_\mathrm{floor}\, U_\mathrm{floor}\,
\bigl(T_\mathrm{room} - T_\mathrm{base}\bigr)
\qquad\text{(ground floor)},
\tag{6}
$$

$$
\dot{Q}_\mathrm{ceil} = A_\mathrm{floor}\, U_\mathrm{ceil}\,
\bigl(T_\mathrm{room} - T_\mathrm{attic}\bigr)
\qquad\text{(top floor)}.
\tag{7}
$$

### 2.4 Buffer-space temperature model

The basement and attic temperatures are not fixed. Each is obtained by a linear
interpolation against outdoor temperature between two calibration anchors
$(T_\mathrm{out}^{\,\mathrm{lo}}, T_\mathrm{buf}^{\,\mathrm{lo}})$ and
$(T_\mathrm{out}^{\,\mathrm{hi}}, T_\mathrm{buf}^{\,\mathrm{hi}})$, clamped
outside that range:

$$
T_\mathrm{buf}(T) = T_\mathrm{buf}^{\,\mathrm{lo}}
+ \bigl(T_\mathrm{buf}^{\,\mathrm{hi}} - T_\mathrm{buf}^{\,\mathrm{lo}}\bigr)
\frac{T - T_\mathrm{out}^{\,\mathrm{lo}}}
{T_\mathrm{out}^{\,\mathrm{hi}} - T_\mathrm{out}^{\,\mathrm{lo}}}.
\tag{8}
$$

To represent thermal inertia, the outdoor temperature fed into Eq. (8) is first
low-pass filtered by an exponential moving average with time constant
$\tau$ (`inertia_hours`). For a time step $\Delta t$ the smoothing factor is

$$
\alpha = 1 - e^{-\Delta t / \tau},
\qquad
\tilde{T}_k = \tilde{T}_{k-1} + \alpha\,\bigl(T_k - \tilde{T}_{k-1}\bigr).
\tag{9}
$$

The lagged series $\tilde{T}_k$ then replaces $T$ in Eq. (8).

---

## 3. Ventilation losses

Ventilation introduces outdoor air at temperature $T_\mathrm{out}$ into a room
held at $T_\mathrm{room}$. The power required to warm that air to room
temperature is

$$
\dot{Q}_\mathrm{vent} = c_\mathrm{air}\, V\, n\,
\bigl(T_\mathrm{room} - T_\mathrm{out}\bigr),
\qquad V = A_\mathrm{floor}\, h,
\tag{10}
$$

with $c_\mathrm{air}$ from Eq. (1). The model distinguishes two air-change
mechanisms with different effective rates $n$.

### 3.1 Window airing (*Stoßlüften*)

While a window is wide open the room exchanges air at the high rate
$n_\mathrm{open}$ (`air_changes_per_hour_open`, e.g. $10\ \mathrm{h^{-1}}$).
This only occurs for the daily airing duration $t_\mathrm{air}$ (minutes per
day), so the day-averaged effective rate is

$$
\bar{n}_\mathrm{air} = n_\mathrm{open}\,
\frac{t_\mathrm{air}/60}{24}.
\tag{11}
$$

The corresponding heat-loss coefficient and power are

$$
\mathrm{UA}_\mathrm{air} = c_\mathrm{air}\, V\, \bar{n}_\mathrm{air},
\qquad
\dot{Q}_\mathrm{air} = \mathrm{UA}_\mathrm{air}\,
\bigl(T_\mathrm{room} - T_\mathrm{out}\bigr).
\tag{12}
$$

Only rooms with a configured $t_\mathrm{air} > 0$ contribute.

### 3.2 Envelope infiltration

Continuous (24/7) leakage through an imperfectly sealed envelope is modelled
with a constant baseline rate $n_\mathrm{base}$
(`air_changes_per_hour_baseline`, e.g. $0.4\ \mathrm{h^{-1}}$):

$$
\mathrm{UA}_\mathrm{inf} = c_\mathrm{air}\, V\, n_\mathrm{base},
\qquad
\dot{Q}_\mathrm{inf} = \mathrm{UA}_\mathrm{inf}\,
\bigl(T_\mathrm{room} - T_\mathrm{out}\bigr).
\tag{13}
$$

Purely interior spaces (e.g. the synthetic circulation-area proxy) have no
exterior envelope and are assigned $\dot{Q}_\mathrm{inf} = 0$.

---

## 4. Aggregation and total demand

For computational efficiency the per-room losses are aggregated into linear
coefficients of the form $\dot{Q} = A_\ast - B_\ast\, T_\mathrm{ref}$, where the
constants accumulate over all rooms $r$:

$$
A_\ast = \sum_r \mathrm{UA}_{\ast,r}\, T_{\mathrm{room},r},
\qquad
B_\ast = \sum_r \mathrm{UA}_{\ast,r}.
\tag{14}
$$

Each $\mathrm{UA}_{\ast,r}$ is the appropriate coefficient from the preceding
sections, e.g. $\mathrm{UA}_{\mathrm{env},r} = A_\mathrm{wall}^{\mathrm{net}} U_\mathrm{wall} + A_\mathrm{win} U_\mathrm{win}$.
The reference temperature $T_\mathrm{ref}$ is $T_\mathrm{out}$ for the envelope,
infiltration, and airing streams, and the respective buffer temperature for the
floor and ceiling streams. The instantaneous building heat demand is the
non-negative sum of all streams (no active cooling is modelled):

$$
\dot{Q}_\mathrm{total} = \max\!\Bigl(
\dot{Q}_\mathrm{env}
+ \dot{Q}_\mathrm{inf}
+ \dot{Q}_\mathrm{air}
+ \dot{Q}_\mathrm{floor}
+ \dot{Q}_\mathrm{ceil},
\ 0 \Bigr).
\tag{15}
$$

Annual or monthly energy follows by integrating $\dot{Q}_\mathrm{total}$ over the
hourly series and, optionally, masking out hours outside the heating season.

---

## 5. Domestic hot-water (DHW) energy

DHW energy is derived from a measured monthly cold-water consumption profile
$V_\mathrm{tot}(m)$ [m³] for calendar month $m$. Only a seasonal fraction
$f(m)$ of the metered water is heated (summer use includes unheated
garden/outdoor water):

$$
f(m) =
\begin{cases}
f_\mathrm{summer}, & m \in \text{summer months},\\[2pt]
f_\mathrm{winter}, & \text{otherwise.}
\end{cases}
\tag{16}
$$

The heated volume and the thermal energy needed to raise it from the cold mains
inlet temperature $T_\mathrm{in}$ to the delivery temperature $T_\mathrm{dhw}$
are

$$
V_\mathrm{hot}(m) = f(m)\, V_\mathrm{tot}(m),
\tag{17}
$$

$$
Q_\mathrm{dhw}(m) = c_\mathrm{w}\, V_\mathrm{hot}(m)\,
\bigl(T_\mathrm{dhw} - T_\mathrm{in}\bigr),
\tag{18}
$$

with $c_\mathrm{w}$ from Eq. (2). The monthly profile $V_\mathrm{tot}(m)$ is
built by averaging each calendar month over all available metering years.

---

## 6. Modelling assumptions and limitations

- **Quasi-steady state.** Each hour is treated as an independent steady-state
  balance; thermal mass enters only through the buffer-temperature lag,
  Eq. (9). Storage within heated rooms is neglected, which is acceptable for
  annual-energy and peak-load estimates.
- **No solar or internal gains.** Free heat from insolation, occupants, and
  appliances is not credited, so the model is mildly conservative.
- **Linear temperature dependence.** All losses are linear in $\Delta T$;
  radiative and convective coefficients are lumped into the U-values and the
  volumetric air capacity.
- **DHW decoupled from space heating.** DHW energy uses metered water volumes
  rather than a draw-profile simulation, and its electricity demand is computed
  separately via the heat-pump COP at the DHW flow temperature.

---

*Source references:* transmission and ventilation — `house_model.py`
(`Room.loss_w`, `Room.airing_ua`, `Room.infiltration_ua`, `BufferModel`);
DHW — `water.py` (`hot_water_energy_kwh`). Configuration parameters are defined
in `config/house_config_*.toml`.
