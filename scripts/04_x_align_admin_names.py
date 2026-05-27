import pandas as pd
import geopandas as gpd
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
        return Path(f"data/clean/acled/HRP_2_countries/{country}.csv")
    for path in base_dir.glob(f"**/{country}_geocoded.csv"):
        return path
    for path in base_dir.glob(f"**/{country}.csv"):
        return path
    return base_dir / f"HRP_2_countries/{country}.csv"

def align_admin_names():
    print(f"🚀 Aligning Admin names for {ISO3} ({COUNTRY}) using spatial joins...")
    
    # 1. Load Data
    acled_path = find_acled_file(country_safe)
    bounds_path = Path(f"data/raw/boundaries/{ISO3}_admin2.geojson")
    
    out_dir = Path("artifacts") / ISO3
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "admin_mapping.json"
    
    if not acled_path.exists() or not bounds_path.exists():
        print(f"✗ Missing required data files for alignment.")
        print(f"  ACLED: {acled_path}")
        print(f"  Boundaries: {bounds_path}")
        return

    acled = pd.read_csv(acled_path)
    bounds = gpd.read_file(bounds_path)

    # Detect admin2 name column in boundaries
    name_col = next(
        (c for c in bounds.columns
         if any(x in c.lower() for x in ["adm2_en", "adm2_name", "name_2", "shapename", "admin2name"])),
        bounds.columns[0]
    )
    print(f"  Using boundary name column: '{name_col}'")

    # 2. Filter ACLED for valid coordinates
    acled_pts = acled.dropna(subset=['Latitude', 'Longitude']).copy()
    
    # Use only recent events to ensure we're matching the latest boundary names
    # but enough to cover all regions
    acled_gdf = gpd.GeoDataFrame(
        acled_pts, 
        geometry=gpd.points_from_xy(acled_pts.Longitude, acled_pts.Latitude),
        crs="EPSG:4326"
    )

    if bounds.crs != acled_gdf.crs:
        bounds = bounds.to_crs(acled_gdf.crs)

    # 3. Spatial Join: Which polygon contains each ACLED point?
    # This tells us: "ACLED says this point is in 'Tapoa', but the GeoJSON says it's in 'Gobnangou'"
    print("  Performing spatial join...")
    joined = gpd.sjoin(acled_gdf, bounds[[name_col, "geometry"]], how="left", predicate="within")

    # 4. Aggregate: For each ACLED 'Admin2' name, what is the most common GeoJSON name?
    mapping = {}
    
    # Group by the name ACLED uses
    for acled_name, group in joined.groupby("Admin2"):
        # Find the most frequent official name in this group
        official_names = group[name_col].dropna()
        if not official_names.empty:
            top_name = official_names.mode()[0]
            if acled_name != top_name:
                mapping[acled_name] = top_name
                print(f"  [Match] {acled_name} -> {top_name}")

    # 5. Save results
    result = {
        "iso3": ISO3,
        "acled_to_official": mapping,
        "official_to_acled": {v: k for k, v in mapping.items()}
    }

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Dynamic alignment complete. Saved to {out_path}")
    print(f"   Found {len(mapping)} mismatches.")

if __name__ == "__main__":
    align_admin_names()
