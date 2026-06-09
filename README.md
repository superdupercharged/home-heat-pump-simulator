# home-heat-pump-simulator

DIY hourly heat pump simulation for South Germany home (Ulm-Memmingen area). Per-room modeling, weather data, DHW, ventilation, and cost estimate.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

### Full simulation (house + heat pump)

Main entry point. Prints annual energy, JAZ, DHW, electricity bill, and saves a plot to `output/sim_full_year.png`.

```bash
# Default house config (template, no DHW/ventilation)
.venv/bin/python run_simulation.py full_year

# Your house (Rehgräble) — use this for real results
HOUSE_CONFIG=house_config_rehgraeble.toml .venv/bin/python run_simulation.py full_year

# Worst-case month (coldest hour per month)
HOUSE_CONFIG=house_config_rehgraeble.toml .venv/bin/python run_simulation.py worst_case
```

`HOUSE_CONFIG` selects a file from `config/` (filename only is enough). Without it, `config/house_config.toml` is used — that template has no hot water or ventilation configured.

### House model only (no heat pump)

Heat demand from weather only; plot in `output/house_full_year.png`.

```bash
HOUSE_CONFIG=house_config_rehgraeble.toml .venv/bin/python run_house.py full_year
HOUSE_CONFIG=house_config_rehgraeble.toml .venv/bin/python run_house.py worst_case
```

### Radiator adequacy check

Compares radiator output at 55/58 °C flow against room design heat load. Plot in `output/radiator_check.png`.

```bash
HOUSE_CONFIG=house_config_rehgraeble.toml .venv/bin/python radiator_check.py
```

### Heat pump COP table (datasheet fit)

Reads `config/config.toml`, fits the Midea curve to datasheet points, prints COP/power table to the terminal.

```bash
.venv/bin/python home_heat_sim.py
```

## Configure

All config files live in `config/`:

| File | Purpose |
|------|---------|
| `config/config.toml` | Heat pump, heating curve (`[heating_curve]`), electricity price |
| `config/house_config_rehgraeble.toml` | Your house: rooms, U-values, ventilation, DHW |
| `config/house_config.toml` | Generic template house |

The heating curve (`[heating_curve]` in `config/config.toml`) sets flow temperature from a damped outdoor temp (default 24 h lag). `flow_at_design_c` is the design-point Vorlauf (NAT) and is also used by `radiator_check.py`. Plots: `output/heating_curve.png`, `output/sim_yearly_temps.png`.

Datasheets and weather data live in `source_data/`.
