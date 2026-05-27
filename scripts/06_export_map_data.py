"""
06_export_map_data.py
=====================
Joins hybrid vulnerability scores onto admin2 boundaries and exports:
  - artifacts/data.geojson   → choropleth map layer (Leaflet-ready)
  - artifacts/trends.json    → time-series data for the chart panel
  - artifacts/insights.json  → headline stats and key findings
  - artifacts/schools.geojson → school point locations

Supports dynamic ISO3 input. Defaults to Admin2 resolution.
"""

import json
import argparse
import os
import pandas as pd
import numpy as np
import geopandas as gpd
from pathlib import Path
from shapely.geometry import mapping

# ── Config ────────────────────────────────────────────────────────────────────
ISO3         = os.environ.get("PIPELINE_ISO3", "BFA")
COUNTRY      = os.environ.get("PIPELINE_COUNTRY", "Burkina Faso")
OUT_DIR      = Path("artifacts") / ISO3
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Naming alignment for Burkina Faso (Legacy Fallback)
DEFAULT_NAME_MAP = {
    "Kossi": "Kossin",
    "Oubritenga": "Bassitenga",
    "Sanmatenga": "Sandbondtenga",
    "Soum": "Djelgodji",
    "Tapoa": "Gobnangou",
}

def build_geojson(vuln: pd.DataFrame, boundaries: gpd.GeoDataFrame) -> dict:
    """
    Join vulnerability scores onto admin2 polygons for the choropleth.
    """
    # Load Dynamic Mapping if exists (Priority 1)
    mapping_path = OUT_DIR / "admin_mapping.json"
    acled_to_official = {}
    
    if mapping_path.exists():
        with open(mapping_path, 'r') as f:
            mapping_data = json.load(f)
            # Ensure it matches the requested ISO3
            if mapping_data.get("iso3") == ISO3:
                acled_to_official = mapping_data.get("acled_to_official", {})
                print(f"  [Info] Loaded {len(acled_to_official)} name mappings dynamically for {ISO3}.")
            else:
                print(f"  [Warning] admin_mapping.json is for {mapping_data.get('iso3')}, not {ISO3}. Skipping.")

    # Fallback to hardcoded map only for BFA if no mapping exists
    if not acled_to_official and ISO3 == "BFA":
        acled_to_official = DEFAULT_NAME_MAP

    # Detect admin2 name column in boundaries
    name_col = next(
        (c for c in boundaries.columns
         if any(x in c.lower() for x in ["adm2_en", "adm2_name", "name_2", "shapename", "admin2name"])),
        boundaries.columns[0]
    )
    if "Admin2_Geo" not in boundaries.columns:
        boundaries = boundaries.rename(columns={name_col: "Admin2_Geo"})

    boundaries["Admin2_Geo"] = boundaries["Admin2_Geo"].str.strip().str.title()

    vuln["Admin2_Mapped"] = vuln["Admin2"].map(acled_to_official).fillna(vuln["Admin2"])
    vuln["Admin2_Mapped"] = vuln["Admin2_Mapped"].str.strip().str.title()

    # --- Categorization Logic ---
    # We use medians as thresholds to classify the nature of the risk
    conf_median = vuln["conflict_score"].median()
    frag_median = vuln["density_fragility"].median()

    def get_risk_type(row):
        is_high_conf = row["conflict_score"] > conf_median
        is_high_frag = row["density_fragility"] > frag_median
        
        if is_high_conf and is_high_frag: return "Double Jeopardy"
        if is_high_conf:                 return "Flashpoint"
        if is_high_frag:                 return "Structurally Fragile"
        return "Stable/Monitor"

    # Build GeoJSON manually
    features = []
    for _, row in vuln.iterrows():
        match = boundaries[boundaries["Admin2_Geo"] == row["Admin2_Mapped"]]
        if match.empty:
            continue
            
        geom = match.iloc[0].geometry
        if geom is None:
            continue

        properties = {
            "admin2":        row["Admin2"], 
            "admin1":        row["Admin1"],
            "year":          int(row["year"]),
            "score":         round(float(row["score"]), 3),
            "priority":      str(row["priority"]),
            "risk_type":     get_risk_type(row),
            "events":        int(row["events"]) if pd.notna(row["events"]) else 0,
            "fatalities":    int(row["fatalities"]) if pd.notna(row["fatalities"]) else 0,
            "events_2yr":    int(row["events_2yr"]) if "events_2yr" in row and pd.notna(row["events_2yr"]) else 0,
            "fatalities_2yr": int(row["fatalities_2yr"]) if "fatalities_2yr" in row and pd.notna(row["fatalities_2yr"]) else 0,
            "oos_raw":       int(row["oos_raw"]) if "oos_raw" in row and pd.notna(row["oos_raw"]) else None,
            "persistence_raw": round(float(row["persistence_raw"]), 1) if "persistence_raw" in row and pd.notna(row["persistence_raw"]) else None,
            "enrolment_raw":   round(float(row["enrolment_raw"]), 1) if "enrolment_raw" in row and pd.notna(row["enrolment_raw"]) else None,
            "edu_baseline":  round(float(row["edu_baseline"]), 3),
            "conflict_score": round(float(row["conflict_score"]), 3),
            "density_fragility": round(float(row["density_fragility"]), 3) if "density_fragility" in row and pd.notna(row["density_fragility"]) else 0.5,
            "schools_per_1000": round(float(row["schools_per_1000_children"]), 2) if "schools_per_1000_children" in row and pd.notna(row["schools_per_1000_children"]) else 0,
            "school_age_pop": int(row["school_age_pop"]) if "school_age_pop" in row and pd.notna(row["school_age_pop"]) else 0,
            "score_basis":   str(row["score_basis"])
        }
        
        if "school_count" in row:
            properties["school_count"] = int(row["school_count"])

        features.append({
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": properties
        })

    return {"type": "FeatureCollection", "features": features}


def build_province_school_risk(school_scores: list, all_schools_df: pd.DataFrame, admin2_path: str) -> dict:
    """Groups all schools by province, year, and risk category."""
    # Structure: { province: { year: { stable: n, high: n, critical: n } } }
    risk_data = {"National": {}}
    
    # 1. Spatial Join to get Province for all schools
    schools_gdf = gpd.GeoDataFrame(
        all_schools_df, 
        geometry=gpd.points_from_xy(all_schools_df.longitude, all_schools_df.latitude),
        crs="EPSG:4326"
    )
    boundaries = gpd.read_file(admin2_path)
    if boundaries.crs != schools_gdf.crs:
        boundaries = boundaries.to_crs(schools_gdf.crs)
        
    name_col = next(
        (c for c in boundaries.columns
         if any(x in c.lower() for x in ["adm2_en", "adm2_name", "name_2", "shapename", "admin2name"])),
        boundaries.columns[0]
    )
    
    joined = gpd.sjoin(schools_gdf, boundaries[[name_col, "geometry"]], how="left", predicate="within")
    
    # Use mapping if available to align ACLED/Analysis names with OCHA names
    mapping_path = OUT_DIR / "admin_mapping.json"
    official_to_acled = {}
    if mapping_path.exists():
        with open(mapping_path, 'r') as f:
            mapping_data = json.load(f)
            official_to_acled = mapping_data.get("official_to_acled", {})

    all_schools_df['province_official'] = joined[name_col].str.strip().str.title().fillna("Unknown")
    all_schools_df['province'] = all_schools_df['province_official'].map(official_to_acled).fillna(all_schools_df['province_official'])

    # 2. Process all schools
    # Build a lookup for assessed schools (those near conflict)
    # Using coords as key for better matching
    assessed = {}
    for s in school_scores:
        lat = s.get('lat', s.get('latitude'))
        lon = s.get('lon', s.get('longitude'))
        if lat is not None and lon is not None:
            key = f"{round(float(lat), 4)},{round(float(lon), 4)}"
            assessed[key] = s
    
    for _, school in all_schools_df.iterrows():
        prov = school['province']
        key = f"{round(school['latitude'], 4)},{round(school['longitude'], 4)}"
        
        if prov not in risk_data: risk_data[prov] = {}
        
        # Get yearly scores if available, otherwise default to stable
        yearly_scores = {}
        if key in assessed:
            yearly_scores = assessed[key].get('yearly_scores', {})
        
        # We need a defined range of years to ensure consistency
        available_years = sorted(yearly_scores.keys()) if yearly_scores else [str(y) for y in range(2015, 2027)]
        
        for yr_str in available_years:
            score = float(yearly_scores.get(yr_str, 0))
            
            # Use the same thresholds as the aggregation script
            category = "stable"
            if score > 0.6:   category = "critical"
            elif score > 0.3: category = "high"
            
            # Province stats
            if yr_str not in risk_data[prov]:
                risk_data[prov][yr_str] = {"stable": 0, "high": 0, "critical": 0}
            risk_data[prov][yr_str][category] += 1
            
            # National stats
            if yr_str not in risk_data["National"]:
                risk_data["National"][yr_str] = {"stable": 0, "high": 0, "critical": 0}
            risk_data["National"][yr_str][category] += 1
            
    return risk_data


def build_trends_json(trends: pd.DataFrame, school_risk_counts: dict) -> list[dict]:
    """Convert national trends DataFrame to a JSON array for the chart."""
    # Round all numeric columns and replace NaN with None for valid JSON (null)
    trends = trends.round(3).replace({np.nan: None})
    records = trends.to_dict(orient="records")
    
    # Add accurate school risk counts to each year
    for rec in records:
        yr = str(rec["year"])
        rec["high_risk_schools"] = school_risk_counts.get(yr, 0)
        
    return records


def build_insights(vuln: pd.DataFrame, trends: pd.DataFrame, iso3: str, school_risk_counts: dict) -> dict:
    """
    Compute headline stats for the summary panel.
    """
    latest_year  = vuln["year"].max()
    vuln_latest  = vuln[vuln["year"] == latest_year]
    # In our 4-tier system: critical, high_priority, medium_priority, lower_priority
    critical     = vuln_latest[vuln_latest["priority"] == "critical"]
    high_priority = vuln_latest[vuln_latest["priority"] == "high_priority"]

    # Conflict trend: compare last 3 years to prior 3 years
    recent    = trends[trends["year"] >= latest_year - 2]["total_events"].mean()
    prior     = trends[trends["year"].between(latest_year - 5, latest_year - 3)]["total_events"].mean()
    trend_pct = round((recent - prior) / prior * 100, 1) if prior and prior > 0 else 0

    # Unified Schools at risk count (v_score > 0.7)
    schools_at_risk = school_risk_counts.get(str(latest_year), 0)

    return {
        "country":           iso3,
        "analysis_year":     int(latest_year),
        "critical_regions":  int(len(critical)),
        "high_risk_regions": int(len(high_priority)),
        "total_regions":     int(len(vuln_latest)),
        "conflict_trend_pct": trend_pct,
        "top_3_critical":    critical.nlargest(3, "score")["Admin2"].tolist(),
        "total_events":      int(trends[trends["year"] == latest_year]["total_events"].sum()),
        "total_fatalities":  int(trends[trends["year"] == latest_year]["total_fatalities"].sum()),
        "schools_at_risk":   int(schools_at_risk)
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export analysis results to web formats.")
    parser.add_argument("--iso3", default=ISO3, help="ISO3 country code")
    args = parser.parse_args()
    iso3 = args.iso3.upper()

    print(f"Exporting map data for {iso3}...")

    in_vuln   = OUT_DIR / f"{iso3}_hybrid_vulnerability_index.csv"
    in_trends = OUT_DIR / f"{iso3}_national_trends.csv"
    in_admin2 = Path(f"data/raw/boundaries/{iso3}_admin2.geojson")
    in_schools = Path(f"data/clean/schools/schools_{iso3}.csv")
    # school_vulnerability_scores.json is generic for the dashboard
    in_school_scores = OUT_DIR / "school_vulnerability_scores.json"

    if not in_vuln.exists():
        print(f"✗ Vulnerability file missing: {in_vuln}")
        exit(1)
    if not in_trends.exists():
        print(f"✗ National trends file missing: {in_trends}")
        exit(1)

    vuln   = pd.read_csv(in_vuln)
    trends = pd.read_csv(in_trends)
    
    # Calculate accurate yearly school risk counts (v_score > 0.7)
    school_risk_counts = {}
    if in_school_scores.exists():
        print(f"  → Calculating unified school-at-risk counts...")
        with open(in_school_scores, "r") as f:
            scores_data = json.load(f)
        
        for s in scores_data:
            # Count for each year the school is considered at risk
            for yr in s.get("at_risk_years", []):
                school_risk_counts[str(yr)] = school_risk_counts.get(str(yr), 0) + 1

    school_counts = None
    if in_schools.exists() and in_admin2.exists():
        print(f"  → Processing school locations for {iso3}...")
        boundaries = gpd.read_file(in_admin2)
        # Detect admin2 name column
        name_col = next(
            (c for c in boundaries.columns
             if any(x in c.lower() for x in ["adm2_en", "adm2_name", "name_2", "shapename", "admin2name"])),
            boundaries.columns[0]
        )
        
        schools_df = pd.read_csv(in_schools)
        schools_gdf = gpd.GeoDataFrame(
            schools_df, 
            geometry=gpd.points_from_xy(schools_df.longitude, schools_df.latitude),
            crs="EPSG:4326"
        )
        
        # Ensure CRS match
        if boundaries.crs != schools_gdf.crs:
            boundaries = boundaries.to_crs(schools_gdf.crs)
            
        # Spatial join to count schools per admin2
        print(f"  → Performing spatial join (schools ∩ boundaries)...")
        joined = gpd.sjoin(schools_gdf, boundaries[[name_col, "geometry"]], how="inner", predicate="within")
        school_counts = joined.groupby(name_col).size().reset_index(name="school_count_new")
        school_counts = school_counts.rename(columns={name_col: "Admin2_Geo"})
        school_counts["Admin2_Geo"] = school_counts["Admin2_Geo"].str.strip().str.title()
        
        # Merge school counts into vuln early
        mapping_path = OUT_DIR / "admin_mapping.json"
        acled_to_official = DEFAULT_NAME_MAP
        if mapping_path.exists():
            with open(mapping_path, 'r') as f:
                acled_to_official = json.load(f).get("acled_to_official", {})

        vuln["Admin2_Mapped"] = vuln["Admin2"].map(acled_to_official).fillna(vuln["Admin2"])
        vuln["Admin2_Mapped"] = vuln["Admin2_Mapped"].str.strip().str.title()
        
        # Merge the new count from spatial join
        vuln = vuln.merge(school_counts, left_on="Admin2_Mapped", right_on="Admin2_Geo", how="left")
        
        # If school_count already exists (from hybrid index), we prioritize the spatial join result
        if "school_count" in vuln.columns:
            vuln["school_count"] = vuln["school_count_new"].fillna(vuln["school_count"])
        else:
            vuln["school_count"] = vuln["school_count_new"]
            
        vuln["school_count"] = vuln["school_count"].fillna(0).astype(int)
        vuln = vuln.drop(columns=["school_count_new", "Admin2_Geo"], errors="ignore")

        # Export schools.geojson (all points, minimal properties for performance)
        schools_out_path = OUT_DIR / "schools.geojson"
        print(f"  → Saving schools point data...")
        
        # Round coordinates for schools too
        def round_geom(geom):
            from shapely.geometry import shape, mapping
            from shapely.ops import transform
            def round_coords(*args):
                return tuple(round(c, 4) for c in args)
            return transform(round_coords, geom)

        schools_gdf["geometry"] = schools_gdf["geometry"].apply(round_geom)
        
        # Use GeoPandas for export
        schools_out_gdf = gpd.GeoDataFrame(
            schools_gdf[["name", "amenity", "geometry"]],
            crs="EPSG:4326"
        )
        schools_out_gdf.to_file(schools_out_path, driver="GeoJSON")
        print(f"  ✓ Schools GeoJSON → {schools_out_path}")

    # ── GeoJSON ──────────────────────────────────────────────────────────────
    if in_admin2.exists():
        boundaries = gpd.read_file(in_admin2)
        
        # Simplify geometry to reduce file size (0.005 degrees is ~500m)
        print("  → Simplifying boundary geometries...")
        boundaries["geometry"] = boundaries["geometry"].simplify(0.005, preserve_topology=True)
        
        # Renaming for consistency with build_geojson
        name_col = next(
            (c for c in boundaries.columns
             if any(x in c.lower() for x in ["adm2_en", "adm2_name", "name_2", "shapename", "admin2name"])),
            boundaries.columns[0]
        )
        boundaries = boundaries.rename(columns={name_col: "Admin2_Geo"})

        geojson    = build_geojson(vuln, boundaries)
        geojson_path = OUT_DIR / "data.geojson"
        with open(geojson_path, "w") as f:
            json.dump(geojson, f, separators=(",", ":"))
        print(f"  ✓ GeoJSON ({len(geojson['features'])} features) → {geojson_path}")
    else:
        print(f"  ⚠ No Admin2 boundary file at {in_admin2} — skipping GeoJSON")

    # ── Trends JSON ───────────────────────────────────────────────────────────
    trends_json = build_trends_json(trends, school_risk_counts)
    trends_path = OUT_DIR / "trends.json"
    with open(trends_path, "w") as f:
        json.dump(trends_json, f, separators=(",", ":"))
    print(f"  ✓ Trends ({len(trends_json)} years) → {trends_path}")

    # ── Province School Risk JSON ─────────────────────────────────────────────
    if in_school_scores.exists() and in_schools.exists() and in_admin2.exists():
        print(f"  → Generating province school risk trajectory...")
        with open(in_school_scores, "r") as f:
            scores_data = json.load(f)
        all_schools_df = pd.read_csv(in_schools)
        risk_data = build_province_school_risk(scores_data, all_schools_df, str(in_admin2))
        out_risk = OUT_DIR / "province_school_risk.json"
        with open(out_risk, "w") as f:
            json.dump(risk_data, f, indent=2)
        print(f"  ✓ Province Risk JSON → {out_risk}")

    # ── Insights JSON ─────────────────────────────────────────────────────────
    insights = build_insights(vuln, trends, iso3, school_risk_counts)
    insights_path = OUT_DIR / "insights.json"
    with open(insights_path, "w") as f:
        json.dump(insights, f, indent=2)
    print(f"  ✓ Insights → {insights_path}")
    print(f"\n  Headline: {insights['critical_regions']} regions CRITICAL, {insights['high_risk_regions']} HIGH priority")
