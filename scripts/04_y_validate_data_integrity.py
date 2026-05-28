"""
04_y_validate_data_integrity.py
==============================
Acts as a 'Quality Gate' for the pipeline. Checks all processed data 
for existence, format integrity, and essential content before analysis starts.

Inputs: data/clean/*, data/raw/boundaries/*, artifacts/admin_mapping.json
"""

import os
import pandas as pd
import geopandas as gpd
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
ISO3    = os.environ.get("PIPELINE_ISO3", "BFA")
COUNTRY = os.environ.get("PIPELINE_COUNTRY", "Burkina Faso")
country_safe = COUNTRY.replace(" ", "_")

def validate():
    print(f"🔍 Validating data integrity for {ISO3} ({COUNTRY})...")
    
    errors = []
    warnings = []

    # 1. Check Conflict Data
    acled_path = None
    base_dir = Path("data/clean/acled")
    if base_dir.exists():
        for p in base_dir.glob(f"**/{country_safe}*.csv"):
            acled_path = p
            break
    
    if not acled_path or not acled_path.exists():
        errors.append(f"CRITICAL: Conflict data missing for {COUNTRY} (expected {country_safe}.csv)")
    else:
        try:
            df = pd.read_csv(acled_path)
            cols = set(df.columns)
            required = {"Year", "Admin1", "Admin2"} # Basic minimums
            missing = required - cols
            if missing:
                errors.append(f"CRITICAL: Conflict file {acled_path.name} missing columns: {missing}")
            if len(df) == 0:
                errors.append(f"CRITICAL: Conflict file {acled_path.name} is empty.")
            print(f"  ✓ Conflict data: Valid ({len(df)} records)")
        except Exception as e:
            errors.append(f"CRITICAL: Could not read conflict file: {e}")

    # 2. Check Education Data
    edu_path = Path("data/clean/education/master_education.csv")
    if not edu_path.exists():
        errors.append("CRITICAL: master_education.csv missing.")
    else:
        df_edu = pd.read_csv(edu_path)
        country_edu = df_edu[df_edu['iso3'] == ISO3]
        if len(country_edu) == 0:
            errors.append(f"CRITICAL: No education indicators found for {ISO3} in master file.")
        else:
            indicators = country_edu['indicator'].unique()
            print(f"  ✓ Education data: Valid ({len(indicators)} indicators found)")

    # 3. Check Boundaries
    bounds_path = Path(f"data/raw/boundaries/{ISO3}_admin2.geojson")
    if not bounds_path.exists():
        errors.append(f"CRITICAL: Admin2 boundaries missing ({bounds_path})")
    else:
        try:
            gdf = gpd.read_file(bounds_path)
            if len(gdf) == 0:
                errors.append("CRITICAL: Boundary GeoJSON is empty.")
            print(f"  ✓ Boundaries: Valid ({len(gdf)} polygons)")
        except Exception as e:
            errors.append(f"CRITICAL: Boundary GeoJSON is corrupt: {e}")

    # 4. Check Mapping (Optional but recommended)
    mapping_path = Path("artifacts") / ISO3 / "admin_mapping.json"
    if not mapping_path.exists():
        warnings.append("WARNING: admin_mapping.json not found. Spatial joins may fail if names differ.")
    else:
        print("  ✓ Admin mapping: Found")

    # 5. Check Schools
    school_path = Path(f"data/clean/schools/schools_{ISO3}.csv")
    if not school_path.exists():
        warnings.append(f"WARNING: No cleaned school data found for {ISO3}.")
    else:
        df_s = pd.read_csv(school_path)
        if len(df_s) < 5:
             warnings.append(f"WARNING: Very low school count ({len(df_s)}). Is the fetch correct?")
        else:
             print(f"  ✓ School data: Valid ({len(df_s)} locations)")

    # ── Final Report ──────────────────────────────────────────────────────────
    print(f"\n{'='*40}")
    if errors:
        print(f"❌ VALIDATION FAILED ({len(errors)} errors)")
        for e in errors:
            print(f"  - {e}")
        return False
    else:
        print("✅ DATA INTEGRITY PASSED")
        if warnings:
            for w in warnings:
                print(f"  - {w}")
        print(f"{'='*40}\n")
        return True

if __name__ == "__main__":
    success = validate()
    if not success:
        exit(1)
