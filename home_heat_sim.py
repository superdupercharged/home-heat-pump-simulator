import numpy as np
import pandas as pd
import re
import matplotlib.pyplot as plt

# Note: pip install hplib for full realism
# import hplib as hpl

class Room:
    def __init__(self, name, floor_area, height, wall_area, wall_u, window_area, window_u, heater_kw=0):
        self.name = name
        self.heat_loss_coeff = (wall_area * wall_u) + (window_area * window_u)  # W/K
    
    def heat_loss_kw(self, t_inside, t_out):
        delta_t = t_inside - t_out
        return max(0, self.heat_loss_coeff * delta_t / 1000.0)

def parse_home_description(text):
    rooms = []
    room_pattern = re.compile(r'([\w]+ room|kitchen|bedroom|living|bathroom):?\s*(.*?)(?=\w+ room|kitchen|bedroom|living|bathroom|$)', re.I | re.S)
    for match in room_pattern.finditer(text):
        name = match.group(1).strip()
        desc = match.group(2).strip()
        
        floor = float(re.search(r'(\d+\.?\d*)\s*m²?\s*(floor|area)', desc, re.I) or re.search(r'(\d+\.?\d*)', desc) or ['20'])[0] or 20
        height = float(re.search(r'(\d+\.?\d*)\s*m?\s*height|high', desc, re.I) or ['2.5'])[0]
        wall_a = float(re.search(r'(\d+\.?\d*)\s*m²?\s*outer wall|wall', desc, re.I) or ['10'])[0]
        wall_u = float(re.search(r'U=?\s*(\d+\.?\d*)', desc, re.I) or ['0.35'])[0]
        win_a = float(re.search(r'(\d+\.?\d*)\s*m²?\s*window', desc, re.I) or ['3'])[0]
        win_u = float(re.search(r'window.*U=?\s*(\d+\.?\d*)', desc, re.I) or ['1.6'])[0]
        
        rooms.append(Room(name, floor, height, wall_a, wall_u, win_a, win_u))
    return rooms

def simulate_home(rooms, t_inside=21, outside_temps=None):
    if outside_temps is None:
        outside_temps = [10 + 5 * np.sin(2*np.pi*h/8760) + np.random.normal(0,3) for h in range(8760)]
    
    data = []
    for h, t_out in enumerate(outside_temps):
        hour_loss_kw = sum(r.heat_loss_kw(t_inside, t_out) for r in rooms)
        power_kw = hour_loss_kw / 3.5  # simple COP fallback
        cop = 3.5
        
        data.append({
            'hour': h, 't_out': round(t_out,1), 'total_loss_kw': round(hour_loss_kw,3),
            'power_kw': round(power_kw,3), 'cop': round(cop,2)
        })
    
    df = pd.DataFrame(data)
    print(f"Annual heat demand: {df['total_loss_kw'].sum():.0f} kWh")
    print(f"Annual electricity: {df['power_kw'].sum():.0f} kWh")
    return df

if __name__ == "__main__":
    home_desc = """
    Living room: 25m² floor, 2.5m height, 20m² outer wall U=0.28, 6m² windows U=1.4.
    Kitchen: 15m², 2.5m high, 10m² wall U=0.35, 3m² windows.
    Bedroom1: 18m², 2.5m, 12m² wall U=0.3, 4m² windows.
    """
    
    rooms = parse_home_description(home_desc)
    print(f"Parsed {len(rooms)} rooms")
    results = simulate_home(rooms)
    results.to_csv('simulation_results.csv', index=False)
    print("Simulation complete!")
