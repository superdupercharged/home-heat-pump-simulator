# Home Heat Pump Simulator Project Context

## Overview
Lightweight Python tool for simulating heat pump performance in a South Germany (Ulm-Memmingen) home.

- Hourly simulation over a year (8760 points)
- Per-room modeling with UA*ΔT heat loss
- Natural language (prose) home description parsing
- Integration with real weather data (PVGIS/Open-Meteo)
- hplib for realistic heat pump COP and power
- Focus on annual energy, costs, peaks

## Key Decisions
- Simple steady-state sufficient for annual estimates
- Thermal inertia adds lag but minor impact on totals
- Keep it lightweight and relatable

## Current Scripts
See home_heat_sim.py

## Next Steps
- Add real home description
- Load Ulm weather
- Enhance parser
- Add RC model for inertia