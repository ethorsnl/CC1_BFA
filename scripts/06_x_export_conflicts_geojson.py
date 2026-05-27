import pandas as pd
import json
import os
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
ISO3    = os.environ.get("PIPELINE_ISO3", "BFA")
COUNTRY = os.environ.get("PIPELINE_COUNTRY", "Burkina Faso")

# Sanitize country name for file matching
country_safe = COUNTRY.replace(" ", "_")

def find_acled_file(country: str) -> Path:
    """Search for the country's ACLED CSV in any data/clean/acled/* subdirectory."""
    base_dir = Path("data/clean/acled")
    if not base_dir.exists():
        return Path(f"data/clean/acled/HRP_1_countries/{country}.csv")
    for path in base_dir.glob(f"**/{country}_geocoded.csv"):
        return path
    for path in base_dir.glob(f"**/{country}.csv"):
        return path
    return base_dir / f"HRP_1_countries/{country}.csv"

def export_conflicts_geojson():
    print(f"🚀 Exporting HYBRID conflict events GeoJSON for {ISO3} ({COUNTRY})...")
    
    out_dir = Path("artifacts") / ISO3
    out_dir.mkdir(parents=True, exist_ok=True)

    ucdp_path = Path(f"data/raw/conflicts/{ISO3}_granular_conflicts.csv")
    acled_path = find_acled_file(country_safe)
    out_path = out_dir / "conflicts.geojson"
    mapping_path = out_dir / "admin_mapping.json"

    # 1. Load Admin Mapping
    mapping = {}
    if mapping_path.exists():
        with open(mapping_path, 'r', encoding='utf-8') as f:
            mapping = json.load(f).get("official_to_acled", {})

    # 2. Process Data
    # Final list of GeoJSON features
    final_features = []
    
    # 2a. Load UCDP (Preferred Source)
    ucdp_data_by_year_prov = {} # { year: { province: [features] } }
    
    if ucdp_path.exists():
        try:
            df_u = pd.read_csv(ucdp_path)
            if not df_u.empty and 'year' in df_u.columns:
                df_u = df_u.dropna(subset=['latitude', 'longitude'])
                for _, row in df_u.iterrows():
                    yr = int(row['year'])
                    name = str(row.get('adm_2', 'Unknown')).replace(' province', '').replace(' region', '').strip().title()
                    name = mapping.get(name, name)
                    
                    if yr not in ucdp_data_by_year_prov: ucdp_data_by_year_prov[yr] = {}
                    if name not in ucdp_data_by_year_prov[yr]: ucdp_data_by_year_prov[yr][name] = []
                    
                    month = "00"
                    date_str = str(row.get('date_start', ''))
                    if '-' in date_str: month = date_str.split('-')[1]
                    elif '/' in date_str: month = date_str.split('/')[1]

                    ucdp_data_by_year_prov[yr][name].append({
                        "type": "Feature",
                        "properties": {
                            "year": yr,
                            "month": month,
                            "admin2": name,
                            "events": 1,
                            "fatalities": int(row['best']) if pd.notna(row.get('best')) else 0,
                            "is_granular": True,
                            "source": "UCDP"
                        },
                        "geometry": {
                            "type": "Point",
                            "coordinates": [float(row['longitude']), float(row['latitude'])]
                        }
                    })
                print(f"  ✓ Processed UCDP records for {len(ucdp_data_by_year_prov)} years.")
        except Exception as e:
            print(f"  ⚠ Error processing UCDP file: {e}")

    # 2b. Process ACLED (Fallback Source)
    if not acled_path.exists():
        print(f"⚠ ACLED file not found: {acled_path}. Proceeding with UCDP only.")
        df_a = pd.DataFrame(columns=["Year", "Month", "Admin2", "Events", "Fatalities", "Longitude", "Latitude"])
    else:
        df_a = pd.read_csv(acled_path)

    acled_fallback_count = 0
    ucdp_count = 0

    MONTH_MAP = {
        'January': '01', 'February': '02', 'March': '03', 'April': '04',
        'May': '05', 'June': '06', 'July': '07', 'August': '08',
        'September': '09', 'October': '10', 'November': '11', 'December': '12'
    }

    # Combined processing logic:
    # 1. Add all UCDP features
    for yr in ucdp_data_by_year_prov:
        for prov in ucdp_data_by_year_prov[yr]:
            final_features.extend(ucdp_data_by_year_prov[yr][prov])
            ucdp_count += len(ucdp_data_by_year_prov[yr][prov])

    # 2. Add ACLED only if no UCDP exists for that (Year, Province)
    if not df_a.empty:
        for yr, group in df_a.groupby("Year"):
            yr = int(yr)
            for _, row in group.iterrows():
                if row['Events'] <= 0: continue
                
                acled_name = str(row['Admin2']).strip().title()
                mapped_name = mapping.get(acled_name, acled_name)

                # Check if UCDP has data for this year and THIS specific province
                has_ucdp = ucdp_data_by_year_prov.get(yr, {}).get(mapped_name)
                
                if not has_ucdp:
                    final_features.append({
                        "type": "Feature",
                        "properties": {
                            "year": yr,
                            "month": MONTH_MAP.get(str(row['Month']), '00'),
                            "admin2": mapped_name,
                            "events": int(row['Events']),
                            "fatalities": int(row['Fatalities']),
                            "is_granular": False,
                            "source": "ACLED"
                        },
                        "geometry": {
                            "type": "Point",
                            "coordinates": [float(row['Longitude']), float(row['Latitude'])]
                        }
                    })
                    acled_fallback_count += 1

    print(f"  → Merging complete.")
    print(f"    - UCDP (Granular): {ucdp_count} records")
    print(f"    - ACLED (Province Fallback): {acled_fallback_count} records")

    # 3. Save
    geojson = {
        "type": "FeatureCollection",
        "features": final_features
    }

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(geojson, f, separators=(',', ':'))
    
    print(f"✅ Success! Saved {len(final_features)} total events to {out_path}")

if __name__ == "__main__":
    export_conflicts_geojson()
