import pandas as pd
import geopandas as gpd
import os
import json
import requests
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
ISO3    = os.environ.get("PIPELINE_ISO3", "BFA")
COUNTRY = os.environ.get("PIPELINE_COUNTRY", "Burkina Faso")

def get_growth_rates(iso3):
    """Fetch annual population growth % from World Bank API."""
    print(f"  → Fetching annual growth rates from World Bank for {iso3}...")
    try:
        url = f"https://api.worldbank.org/v2/country/{iso3}/indicator/SP.POP.GROW?format=json&per_page=100"
        res = requests.get(url, timeout=10)
        data = res.json()
        if len(data) > 1:
            rates = {int(item['date']): item['value']/100 for item in data[1] if item['value'] is not None}
            return rates
    except Exception as e:
        print(f"  ⚠ Growth rate fetch failed: {e}. Using fallback 2.3%.")
    return {}

def calculate_density_gap():
    print(f"🚀 Calculating continuous relative school density gap for {ISO3} ({COUNTRY})...")
    
    # 1. Paths
    clean_pop_dir = Path(f"data/clean/{ISO3.lower()}_pop_density")
    schools_path  = Path(f"data/clean/schools/schools_{ISO3}.csv")
    bounds_path   = Path(f"data/raw/boundaries/{ISO3}_admin2.geojson")
    
    out_dir = Path("artifacts") / ISO3
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "province_school_fragility.csv"

    # 2. Check for Anchor Data (2020)
    anchor_year = 2020
    anchor_zonal = clean_pop_dir / f"{ISO3.lower()}_zonal_{anchor_year}.csv"
    anchor_heatmap = clean_pop_dir / f"{ISO3.lower()}_pop_{anchor_year}.json"

    if not anchor_zonal.exists() and not anchor_heatmap.exists():
        print(f"✗ Missing anchor population data for {anchor_year}. Run 04_1 first.")
        return

    # 3. Load School and Boundary Data
    schools_df = pd.read_csv(schools_path)
    bounds = gpd.read_file(bounds_path)
    
    name_col = next(
        (c for c in bounds.columns
         if any(x in c.lower() for x in ["adm2_en", "adm2_name", "name_2", "shapename", "admin2name"])),
        bounds.columns[0]
    )

    schools_gdf = gpd.GeoDataFrame(
        schools_df, 
        geometry=gpd.points_from_xy(schools_df.longitude, schools_df.latitude),
        crs="EPSG:4326"
    )
    if bounds.crs != schools_gdf.crs:
        bounds = bounds.to_crs(schools_gdf.crs)
    
    schools_joined = gpd.sjoin(schools_gdf, bounds[[name_col, "geometry"]], how="inner", predicate="within")
    school_counts = schools_joined.groupby(name_col).size().reset_index(name="school_count")

    # 4. Load or Generate Anchor Population (2020)
    if anchor_zonal.exists():
        print(f"  → Loading anchor zonal stats for {anchor_year}...")
        anchor_pop = pd.read_csv(anchor_zonal)
    else:
        print(f"  → Generating anchor zonal stats from heatmap JSON for {anchor_year}...")
        with open(anchor_heatmap, 'r') as f:
            pop_data = json.load(f)
        pop_points = [{"lat": e[0], "lon": e[1], "pop_density": e[2]} for e in pop_data["data"]]
        pop_df = pd.DataFrame(pop_points)
        pop_gdf = gpd.GeoDataFrame(pop_df, geometry=gpd.points_from_xy(pop_df.lon, pop_df.lat), crs="EPSG:4326")
        pop_joined = gpd.sjoin(pop_gdf, bounds[[name_col, "geometry"]], how="inner", predicate="within")
        anchor_pop = pop_joined.groupby(name_col)["pop_density"].sum().reset_index()
        anchor_pop.columns = ["Region", "Population"]

    anchor_2020 = bounds[[name_col]].merge(school_counts, on=name_col, how="left").merge(anchor_pop, left_on=name_col, right_on="Region", how="left")
    anchor_2020["school_count"] = anchor_2020["school_count"].fillna(0)
    anchor_2020["Population"] = anchor_2020["Population"].fillna(0)
    anchor_2020["school_age_pop_anchor"] = anchor_2020["Population"] * 0.25
    
    # 5. Load Other Existing Zonal Files (Priority Source)
    ground_truth = {}
    for zonal_file in clean_pop_dir.glob(f"{ISO3.lower()}_zonal_*.csv"):
        try:
            year_str = zonal_file.stem.split("_")[-1]
            year = int(year_str)
            if year != anchor_year:
                df_yr = pd.read_csv(zonal_file)
                ground_truth[year] = df_yr.set_index("Region")["Population"].to_dict()
                print(f"  → Found ground truth data for {year}")
        except: continue

    # 6. DYNAMIC CHAIN EXTRAPOLATION (2015-2026)
    rates = get_growth_rates(ISO3)
    default_rate = 0.023
    years = list(range(2015, 2027))
    all_years_data = []

    print(f"  → Extrapolating population using the Chain Method (Anchor: {anchor_year})...")
    for _, province_row in anchor_2020.iterrows():
        p_name = province_row[name_col]
        
        # Calculate full chain history first (from 2020 anchor)
        pop_chain = {anchor_year: province_row["school_age_pop_anchor"]}
        
        # Forward Chain (2021-2026)
        for yr in range(anchor_year + 1, 2027):
            rate = rates.get(yr, rates.get(max(rates.keys()) if rates else 2024, default_rate))
            pop_chain[yr] = pop_chain[yr-1] * (1 + rate)
            
        # Backward Chain (2015-2019)
        for yr in range(anchor_year - 1, 2014, -1):
            rate = rates.get(yr+1, rates.get(min(rates.keys()) if rates else 2015, default_rate))
            pop_chain[yr] = pop_chain[yr+1] / (1 + rate)

        for yr in years:
            # PRIORITY: Ground Truth > Chain Extrapolation
            pop_val = ground_truth.get(yr, {}).get(p_name, None)
            if pop_val is not None:
                final_pop = pop_val * 0.25 # Apply child proxy to ground truth
            else:
                final_pop = pop_chain[yr]

            all_years_data.append({
                "Region": p_name,
                "year": yr,
                "school_count": province_row["school_count"],
                "school_age_pop": final_pop
            })

    final_df = pd.DataFrame(all_years_data)
    
    # 7. RELATIVE FRAGILITY LOGIC
    def calculate_annual_scores(group):
        group["schools_per_1000_children"] = (group["school_count"] / (group["school_age_pop"] / 1000)).replace([float('inf'), -float('inf')], 0).fillna(0)
        target = group["schools_per_1000_children"].quantile(0.8)
        if target <= 0: target = 1.0
        group["school_fragility_score"] = (1 - (group["schools_per_1000_children"] / target)).clip(0, 1)
        return group

    # Apply calculations per year
    print("  → Calculating annual fragility scores...")
    results = []
    for yr, group in final_df.groupby("year"):
        results.append(calculate_annual_scores(group))
    final_df = pd.concat(results)

    final_cols = ["Region", "year", "school_fragility_score", "school_count", "schools_per_1000_children", "school_age_pop"]
    final_df[final_cols].to_csv(out_path, index=False)
    print(f"  ✓ Saved Hybrid Ground-Truth/Chain Analysis to {out_path}")

if __name__ == "__main__":
    calculate_density_gap()
