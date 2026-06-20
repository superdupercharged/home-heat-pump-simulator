# home-heat-pump-simulator

DIY hourly heat pump simulation for South Germany home (Ulm-Memmingen area). Per-room modeling, weather data, DHW, ventilation, and cost estimate.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

### Full simulation (house + heat pump)

Main entry point. Prints annual energy, JAZ, DHW, electricity bill, and saves a plot to `outputs/<house>/sim_full_year.png` (e.g. `outputs/rehgraeble/`).

```bash
# Default house config (template, no DHW/ventilation)
.venv/bin/python run_simulation.py full_year

# Your house (Rehgräble) — use this for real results
HOUSE_CONFIG=house_config_rehgraeble.toml .venv/bin/python run_simulation.py full_year

# Worst-case synthetic year (coldest calendar month from each year 2005–2023)
HOUSE_CONFIG=house_config_rehgraeble.toml .venv/bin/python run_simulation.py worst_case
```

`HOUSE_CONFIG` selects a file from `config/` (filename only is enough). Without it, `config/house_config.toml` is used — that template has no hot water or ventilation configured.

### House model only (no heat pump)

Heat demand from weather only; plot in `outputs/<house>/house_full_year.png`.

```bash
HOUSE_CONFIG=house_config_rehgraeble.toml .venv/bin/python run_house.py full_year
HOUSE_CONFIG=house_config_rehgraeble.toml .venv/bin/python run_house.py worst_case
```

### Room check (radiator adequacy + energy split)

Per-room checks at the design point: radiator adequacy (output at 55/58 °C flow vs. room heat load) and per-room energy split (transmission vs. ventilation). Plots in `outputs/<house>/radiator_check.png` and `outputs/<house>/radiator_room_energy.png`.

```bash
HOUSE_CONFIG=house_config_rehgraeble.toml .venv/bin/python room_check.py
```

### Heat pump COP table (datasheet fit)

Reads `config/config.toml`, fits the Midea curve to datasheet points, prints COP/power table to the terminal.

```bash
.venv/bin/python home_heat_sim.py
```

### Cross-house heat pump sizing comparison

Compare peak thermal/electrical draw for every `house_config_*.toml` against the configured heat pump in `config.toml`, plus an optional smaller nominal size (default 12 kW, same COP curve scaled linearly). Writes `outputs/hp_sizing_comparison.png` and prints a text summary.

```bash
.venv/bin/python compare_sizing.py
.venv/bin/python compare_sizing.py --alt-kw 10
.venv/bin/python compare_sizing.py --houses rehgraeble,lukra
```

Per-house simulation plots live under `outputs/<house>/` (e.g. `outputs/rehgraeble/sim_full_year.png`). The sizing comparison is a single cross-house chart in `outputs/`.

## Configure

All config files live in `config/`:

| File | Purpose |
|------|---------|
| `config/config.toml` | Heat pump, heating curve (`[heating_curve]`), electricity price, **weather year** (`[weather]`) |
| `config/house_config_rehgraeble.toml` | Thilo's house: rooms, U-values, ventilation, DHW |
| `config/house_config_lukra.toml` | Lukra / Tobi's house (1993, no cellar); from `source_data/tobis-haus.md` |
| `config/house_config.toml` | Generic template house |

### Weather

Set the calendar year in `config/config.toml`. **Location** (latitude/longitude) comes from the active house config — each `house_config_*.toml` has a `[location]` section:

```toml
# config/house_config_lukra.toml
[location]
label = "Gifhorn, Niedersachsen"
latitude = 52.489
longitude = 10.546
```

```toml
# config/config.toml
[weather]
year = 2022          # Open-Meteo ERA5 hourly temps for this year
# latitude/longitude here are fallback only (when house config has no [location])
```

Use `year = 0` to run the full-year simulation on the PVGIS Typical Meteorological Year (stitched months from 2005–2023) instead of a single calendar year. On first use of a new year, hourly data is downloaded from Open-Meteo and cached in `source_data/weather_{lat}_{lon}_{year}.csv` — one cache file per location.

The **worst-case** plots (`sim_worst_case.png`, `house_worst_case.png`) stitch a synthetic year from the coldest calendar month in each year of `[weather.worst_case]` (default 2005–2023). Each candidate month is scored by:

1. **Heating degree hours** (duration × severity, base 15 °C)
2. **Longest cold spell** below 0 °C (tie-break)
3. **Minimum temperature** (tie-break)

The full hourly dataset (~8760 h) is run through the simulation with buffer inertia.

Weather caches (`source_data/weather_*.csv`, `worst_case_year_*.csv`) are **gitignored** and fetched on demand. The PVGIS TMY file and `water_monthly_default.json` stay in git as fallbacks.

First-time local setup (respects `HOUSE_CONFIG` for location):

```bash
HOUSE_CONFIG=house_config_lukra.toml .venv/bin/python weather.py fetch_history
HOUSE_CONFIG=house_config_lukra.toml .venv/bin/python run_simulation.py full_year
```

(`fetch_history` downloads 2005–2023 and clears any stale worst-case cache.)

Each heated level auto-gets a **circulation proxy** when `[building]` footprint is set: `net floor = footprint × (1 − wall_area_fraction) − sum(room areas)`. `wall_area_fraction` (default 0.12) is the wall/partition share. Covers Flur/Verkehrsfläche without listing every zone. No exterior walls, no radiators; floor/ceiling + infiltration losses only.

The heating curve (`[heating_curve]` in `config/config.toml`) sets flow temperature from a damped outdoor temp (default 24 h lag). `flow_at_design_c` is the design-point Vorlauf (NAT) and is also used by `room_check.py`. Plot: `outputs/<house>/sim_yearly_temps.png` (includes the curve).

Datasheets and weather data live in `source_data/`.

## Website (GitHub Pages)

Static dashboard **“Opels houses”** with a house switcher (Rehgräble, Lukra, …), per-house plots and stats, electricity-price slider, and a shared heat-pump sizing comparison chart.

### GitHub setup (one-time)

Repo **Settings → Pages**:

| Setting | Value |
|---------|-------|
| Source | Deploy from a branch |
| Branch | **`gh-pages`** |
| Folder | **`/ (root)`** |

The `gh-pages` branch is created automatically by the deploy workflow (or by `deploy_docs.sh`). `main` stays source code only.

### Deploy

**Automatic (recommended):** push to `main` — the [deploy workflow](.github/workflows/deploy-pages.yml) fetches weather history for **each** `house_config_*.toml` location, runs `build_docs.py` (all houses + sizing chart), and updates `gh-pages`.

**Manual:**

```bash
./deploy_docs.sh
```

### Local preview

```bash
.venv/bin/python build_docs.py
cd docs && python3 -m http.server 8000
# → http://localhost:8000
# Switch houses in the UI, or deep-link: ?house=lukra
```

Site assets after build:

| Path | Content |
|------|---------|
| `docs/stats.json` | Multi-house stats (`houses.rehgraeble`, `houses.lukra`, …) |
| `docs/assets/<slug>/` | Per-house simulation plots |
| `docs/assets/hp_sizing_comparison.png` | Cross-house sizing chart |

Live site: **https://superdupercharged.github.io/home-heat-pump-simulator/**
