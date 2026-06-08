# House Modeling Plan for Heat Pump Simulator

## Overview
This document summarizes our discussions on modeling the house for the lightweight Python/Streamlit heat pump simulator. Goal: Keep it practical, relatable (prose input), and accurate enough for annual energy estimates, peak loads, and feasibility in Ulm-Memmingen region.

## Core Approach: Per-Room UA*ΔT Modeling
- **Per-room breakdown**: Model each room individually then sum losses.
- **Inputs per room** (via natural language prose):
  - Floor area (m²)
  - Height (m) → volume for potential infiltration/ventilation
  - Outer wall area (m²) + U-value (W/m²K)
  - Window area (m²) + U-value (W/m²K)
  - Optional: Heater/radiator power from datasheet
- **Heat loss calculation**: `loss_kw = (wall_area * wall_u + window_area * window_u) * ΔT / 1000`
- **Advantages**: Simple, intuitive, spots weak rooms easily. Sufficient for yearly kWh and basic hourly sims.

## Prose-to-Params Parser
- User pastes friendly text description.
- Parser extracts numbers using regex + fallbacks.
- Example input:
  ```
  Living room: 25m² floor, 2.5m height, 20m² outer wall U=0.28, 6m² windows U=1.4.
  Kitchen: 15m², 2.5m high, 10m² wall U=0.35, 3m² windows.
  ```
- Future: Enhance with better NLP or LLM call for robustness.

## Thermal Inertia / Dynamics
- Steady-state (current) is good baseline.
- Thermal mass mainly adds lag/smoothing, minor impact (~few %) on annual totals.
- Optional future: Simple RC-model per room for better hourly dynamics.

## Heat Pump Integration
- Use hplib for realistic COP/power curves based on manufacturer data.
- Fallback: Simple COP formula.
- Account for defrost in cold weather (can be added).

## Weather Data
- Hourly temps from Open-Meteo or PVGIS TMY for Ulm/Memmingen.
- Easy CSV upload in Streamlit app.

## Limitations & Scope
- Good enough for feasibility, sizing, cost estimates.
- Not CFD-level (no need for airflow/comfort details here).
- Can extend with solar gains, infiltration, internal loads later.

## Roadmap Items
- Improve parser
- Add RC thermal mass
- Monthly/peak summaries + cost calc (German prices)
- Export full results

This keeps the tool lightweight and user-friendly. Update as we iterate!