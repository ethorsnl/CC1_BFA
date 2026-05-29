import json
import math
import csv
import argparse
import os
import requests
from pathlib import Path
from shapely.geometry import shape, Point

# ─── Configuration & Mappings ──────────────────────────────────────────────────

INDICATOR_MAP = {
    "primary_oos": {
        "total":  "Children out of school (% of primary school age)",
        "female": "Children out of school, female (% of female primary school age)",
        "male":   "Children out of school, male (% of male primary school age)"
    },
    "primary_enrollment": {
        "total":  "Gross enrolment ratio, primary, both sexes (%)",
        "female": "Gross enrolment ratio, primary, female (%)",
        "male":   "Gross enrolment ratio, primary, male (%)"
    }
}

# Key demographics factors if missing from CSV
# BFA Specific proxies as fallback
DEMO_PROXIES = {
    "primary_age_ratio": 0.172,  # 6-11 years share of total pop
    "adol_age_ratio":    0.079,  # 12-14 years share of total pop
    "female_share":       0.50
}

# ─── Data Helpers ─────────────────────────────────────────────────────────────

def get_growth_rates(iso3):
    """Fetch annual population growth % from World Bank API."""
    print(f"  → Fetching annual growth rates from World Bank for {iso3}...")
    try:
        url = f"https://api.worldbank.org/v2/country/{iso3}/indicator/SP.POP.GROW?format=json&per_page=100"
        res = requests.get(url, timeout=10)
        data = res.json()
        if len(data) > 1:
            return {int(item['date']): item['value']/100 for item in data[1] if item['value'] is not None}
    except Exception as e:
        print(f"  ⚠ Growth rate fetch failed: {e}. Using fallback 2.3%.")
    return {}

def fetch_yearly_indicators(iso3, years, csv_path):
    """Extract national indicator values for each year."""
    data = {y: {} for y in years}
    if not os.path.exists(csv_path):
        return data

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            y = int(row['year'])
            if row['iso3'].upper() == iso3.upper() and y in years and row['region'] == 'National':
                name = row['indicator']
                val = float(row['value']) / 100 if row['value'] else 0
                
                # Check against map
                for key, mapping in INDICATOR_MAP.items():
                    if key not in data[y]: data[y][key] = {}
                    for subkey, label in mapping.items():
                        if name == label:
                            data[y][key][subkey] = val
    return data

def load_provinces(iso3):
    """Load administrative boundaries."""
    paths = [
        f"data/raw/boundaries/{iso3}_admin2.geojson",
        f"data/raw/boundaries/{iso3}_admin1.geojson",
        f"artifacts/{iso3}/data.geojson"
    ]
    geojson_path = next((p for p in paths if os.path.exists(p)), None)
    if not geojson_path: raise FileNotFoundError(f"Boundaries for {iso3} missing.")

    with open(geojson_path, 'r') as f:
        data = json.load(f)
    
    provinces = []
    for feature in data['features']:
        props = feature['properties']
        name = props.get('adm2_name') or props.get('admin2') or props.get('NAME_2') or props.get('NAME_1')
        if not name: continue
        provinces.append({'name': name, 'poly': shape(feature['geometry']), 'pop_2020': 0})
    return provinces

def aggregate_anchor_pop(iso3, provinces):
    """Aggregate 2020 population to provinces."""
    pop_path = f"data/clean/{iso3.lower()}_pop_density/{iso3.lower()}_pop_2020.json"
    if not os.path.exists(pop_path): raise FileNotFoundError(f"Anchor population (2020) missing.")

    with open(pop_path, 'r') as f:
        pop_data = json.load(f)
    
    # Grid area calc
    bounds = [p['poly'].centroid.y for p in provinces if p['poly'].is_valid]
    mid_lat = sum(bounds) / len(bounds) if bounds else 12.0
    res_deg = 0.04165 
    pixel_area_km2 = (res_deg * 111.32) * (res_deg * 111.32 * math.cos(math.radians(mid_lat)))
    
    for lat, lon, pop_km2 in pop_data['data']:
        p = Point(lon, lat)
        pop_count = pop_km2 * pixel_area_km2
        for prov in provinces:
            if prov['poly'].contains(p):
                prov['pop_2020'] += pop_count
                break
    return provinces

# ─── Implementation ───────────────────────────────────────────────────────────

def generate():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iso3", default="BFA")
    parser.add_argument("--start", type=int, default=2015)
    parser.add_argument("--end", type=int, default=2026)
    args = parser.parse_args()

    iso3 = args.iso3.upper()
    years = list(range(args.start, args.end + 1))
    
    print(f"🚀 Generating Contextual Stats for {iso3} ({args.start}-{args.end})...")

    # 1. Setup Data
    provinces = load_provinces(iso3)
    provinces = aggregate_anchor_pop(iso3, provinces)
    rates = get_growth_rates(iso3)
    indicators = fetch_yearly_indicators(iso3, years, "data/clean/education/master_education.csv")
    
    default_growth = 0.023
    
    final_output = {}

    for prov in provinces:
        p_name = prov['name']
        final_output[p_name] = {}
        
        # Extrapolate total population for all years using Chain Method
        pop_history = {2020: prov['pop_2020']}
        
        # Forward
        for yr in range(2021, args.end + 1):
            rate = rates.get(yr, rates.get(max(rates.keys()) if rates else 2024, default_growth))
            pop_history[yr] = pop_history[yr-1] * (1 + rate)
        
        # Backward
        for yr in range(2019, args.start - 1, -1):
            rate = rates.get(yr+1, rates.get(min(rates.keys()) if rates else 2015, default_growth))
            pop_history[yr] = pop_history[yr+1] / (1 + rate)

        # 2. Build Yearly Profile
        for yr in years:
            total_pop = pop_history[yr]
            
            # Apply demographic sub-factors
            # Note: In a future version, these ratios could also be fetched from CSV
            primary_pop = total_pop * DEMO_PROXIES["primary_age_ratio"]
            
            yr_inds = indicators.get(yr, {})
            oos_rates = yr_inds.get("primary_oos", {})
            enr_rates = yr_inds.get("primary_enrollment", {})

            # Default rates if missing for that year
            # We use the most recent available or the national average
            def_oos = oos_rates.get("total", 0.28) # BFA average
            def_enr = enr_rates.get("total", 0.87)

            final_output[p_name][yr] = {
                "population": {
                    "total": round(total_pop),
                    "primary_age": round(primary_pop)
                },
                "sex_disaggregated": {
                    "primary_oos": {
                        "total":  round(primary_pop * def_oos),
                        "female": round((primary_pop * DEMO_PROXIES["female_share"]) * oos_rates.get("female", def_oos)),
                        "male":   round((primary_pop * (1-DEMO_PROXIES["female_share"])) * oos_rates.get("male", def_oos))
                    },
                    "primary_enrolled": {
                        "total":  round(primary_pop * def_enr),
                        "female": round((primary_pop * DEMO_PROXIES["female_share"]) * enr_rates.get("female", def_enr)),
                        "male":   round((primary_pop * (1-DEMO_PROXIES["female_share"])) * enr_rates.get("male", def_enr))
                    }
                }
            }

    # 3. Save Artifact
    out_dir = Path(f"artifacts/{iso3}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "contextual_stats.json"
    
    with open(out_path, 'w') as f:
        json.dump(final_output, f, indent=2)
    
    print(f"\n✅ Success! Sidecar stats generated for {len(provinces)} provinces.")
    print(f"   Path: {out_path}")

if __name__ == "__main__":
    generate()
