import pandas as pd
import json
import os
import numpy as np
import geopandas as gpd
from pathlib import Path
from shapely.geometry import Point

ISO3 = os.environ.get("PIPELINE_ISO3", "BFA")

def calculate_proximity():
    print(f"🚀 Calculating school proximity to conflict for {ISO3}...")
    
    in_schools = Path(f"data/clean/schools/schools_{ISO3}.csv")
    in_conflicts = Path(f"data/raw/conflicts/{ISO3}_granular_conflicts.csv")
    out_dir = Path("artifacts") / ISO3
    out_path = out_dir / "proximity_risk_stats.json"

    if not in_schools.exists() or not in_conflicts.exists():
        print(f"  ⚠ Missing input files for proximity analysis. Skipping.")
        return

    # Load data
    schools_df = pd.read_csv(in_schools)
    conflicts_df = pd.read_csv(in_conflicts)
    
    # Filter for recent conflicts (last 3 years)
    current_year = 2026
    conflicts_df = conflicts_df[conflicts_df['year'] >= (current_year - 3)]
    
    if conflicts_df.empty:
        print("  ⚠ No recent conflicts found for proximity analysis.")
        return

    # Convert to GeoDataFrames
    schools_gdf = gpd.GeoDataFrame(
        schools_df, 
        geometry=gpd.points_from_xy(schools_df.longitude, schools_df.latitude),
        crs="EPSG:4326"
    )
    
    conflicts_gdf = gpd.GeoDataFrame(
        conflicts_df, 
        geometry=gpd.points_from_xy(conflicts_df.longitude, conflicts_df.latitude),
        crs="EPSG:4326"
    )
    
    # Project to a local meter-based CRS for accurate distance (UTM zone 30N for BFA)
    # Generic fallback to World Equidistant Cylindrical if not BFA
    if ISO3 == "BFA":
        proj_crs = "EPSG:32630"
    else:
        proj_crs = "EPSG:3857" # Web Mercator (not ideal for distance but okayish)
        
    schools_gdf = schools_gdf.to_crs(proj_crs)
    conflicts_gdf = conflicts_gdf.to_crs(proj_crs)
    
    # Calculate distance to nearest conflict for every school
    print(f"  → Computing distances for {len(schools_gdf)} schools vs {len(conflicts_gdf)} events...")
    
    # Using sjoin_nearest (available in geopandas 0.10+)
    # It returns the distance in the unit of the CRS (meters)
    nearest = gpd.sjoin_nearest(schools_gdf, conflicts_gdf, distance_col="dist_m", how="left")
    
    # Aggregate stats
    avg_dist_km = nearest['dist_m'].mean() / 1000
    median_dist_km = nearest['dist_m'].median() / 1000
    schools_within_5km = len(nearest[nearest['dist_m'] <= 5000])
    schools_within_10km = len(nearest[nearest['dist_m'] <= 10000])
    
    stats = {
        "iso3": ISO3,
        "total_schools": len(schools_df),
        "recent_conflict_events": len(conflicts_df),
        "avg_distance_to_conflict_km": round(float(avg_dist_km), 2),
        "median_distance_to_conflict_km": round(float(median_dist_km), 2),
        "schools_within_5km": schools_within_5km,
        "schools_within_5km_pct": round((schools_within_5km / len(schools_df)) * 100, 1),
        "schools_within_10km": schools_within_10km,
        "last_updated": pd.Timestamp.now().isoformat()
    }
    
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(stats, f, indent=2)
        
    print(f"  ✓ Saved proximity stats to {out_path}")
    print(f"  → Average distance: {avg_dist_km:.1f}km | Schools <5km: {schools_within_5km}")

if __name__ == "__main__":
    calculate_proximity()
