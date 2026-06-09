# home-heat-pump-simulator

DIY hourly heat pump simulation for South Germany home (Ulm-Memmingen area). Prose input, per-room modeling, real weather integration.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
.venv/bin/python home_heat_sim.py
```

This reads `config.toml`, fits the heat-pump COP/power curve to the manufacturer
datasheet points, and prints to the terminal:

1. Model name and the fit reference point (COP_ref, P_el_ref)
2. A fit-vs-datasheet sanity-check table
3. COP / electrical power / thermal power per outdoor temperature at the
   configured flow temperature
4. A COP matrix (outdoor temps x flow temps)

## Configure

Edit `config.toml` to change the scenario, then re-run. Key knob:
`flow_temp_c` (e.g. set it to `55.0` or `58.0`). No code changes needed.

Heat pump datasheets live in `source_data/`.
