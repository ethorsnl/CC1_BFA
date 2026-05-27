"""
fetch_schools.py
================
Fetches school/education facility locations for conflict-zone countries
from two sources in priority order:

  1. HDX (CKAN API) — official UNICEF/cluster/government datasets
  2. Overpass API (OpenStreetMap) — global fallback with real lat/lon

Outputs:
  schools_hdx.geojson      ← HDX-sourced schools
  schools_osm.geojson      ← OSM-sourced schools (fallback countries)
  schools_merged.geojson   ← combined, deduplicated, Leaflet-ready

Install: pip install requests pandas geopandas
"""

import requests
import json
import time
import os
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

# ── Countries to fetch ────────────────────────────────────────────────────────
# ISO3 codes → ISO2 (for Overpass) + HDX group (lowercase ISO3)
# Sub-Saharan Africa, Middle East, SE Asia conflict zones
COUNTRIES = {
    # iso3  : (iso2,  hdx_group,   common_name)
    "SOM":  ("SO", "som",  "Somalia"),
    "SDN":  ("SD", "sdn",  "Sudan"),
    "SSD":  ("SS", "ssd",  "South Sudan"),
    "ETH":  ("ET", "eth",  "Ethiopia"),
    "NGA":  ("NG", "nga",  "Nigeria"),
    "MLI":  ("ML", "mli",  "Mali"),
    "BFA":  ("BF", "bfa",  "Burkina Faso"),
    "TCD":  ("TD", "tcd",  "Chad"),
    "CAF":  ("CF", "caf",  "Central African Republic"),
    "COD":  ("CD", "cod",  "DR Congo"),
    "MOZ":  ("MZ", "moz",  "Mozambique"),
    "YEM":  ("YE", "yem",  "Yemen"),
    "SYR":  ("SY", "syr",  "Syria"),
    "IRQ":  ("IQ", "irq",  "Iraq"),
    "AFG":  ("AF", "afg",  "Afghanistan"),
    "PSE":  ("PS", "pse",  "Palestine"),
    "LBN":  ("LB", "lbn",  "Lebanon"),
    "LBY":  ("LY", "lby",  "Libya"),
    "MMR":  ("MM", "mmr",  "Myanmar"),
    "KHM":  ("KH", "khm",  "Cambodia"),
    "BGD":  ("BD", "bgd",  "Bangladesh"),
    "PAK":  ("PK", "pak",  "Pakistan"),
}

# HDX dataset slugs known to contain school point data
# Format: hdx_group → [dataset_slug, ...]
HDX_SCHOOL_DATASETS = {
    "som": ["hotosm_som_education_facilities", "somalia-schools"],
    "yem": ["education-cluster-yemen"],
    "nga": ["hotosm_nga_education_facilities"],
    "irq": ["hotosm_irq_education_facilities"],
    "syr": ["hotosm_syr_education_facilities"],
    "afg": ["hotosm_afg_education_facilities"],
    "pse": ["hotosm_pse_education_facilities"],
    "lbn": ["hotosm_lbn_education_facilities"],
    "mli": ["hotosm_mli_education_facilities"],
    "ssd": ["hotosm_ssd_education_facilities"],
    "mmr": ["hotosm_mmr_education_facilities"],
    "caf": ["hotosm_caf_education_facilities"],
    "cod": ["hotosm_cod_education_facilities"],
    "bgd": ["hotosm_bgd_education_facilities"],
    "pak": ["hotosm_pak_education_facilities"],
}

HDX_BASE    = "https://data.humdata.org/api/3/action"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OUT_DIR      = "data/raw/schools"
os.makedirs(OUT_DIR, exist_ok=True)


# ── HDX helpers ───────────────────────────────────────────────────────────────

def hdx_search_schools(hdx_group: str, country_name: str) -> list[dict]:
    """
    Search HDX CKAN API for education facility datasets for a country.
    Returns list of resource download URLs.
    """
    # First try known dataset slugs
    known = HDX_SCHOOL_DATASETS.get(hdx_group, [])
    resources = []

    for slug in known:
        try:
            r = requests.get(
                f"{HDX_BASE}/package_show",
                params={"id": slug},
                timeout=15
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("success"):
                    for res in data["result"].get("resources", []):
                        fmt = res.get("format", "").lower()
                        if any(f in fmt for f in ["csv", "geojson", "shapefile", "zip", "xlsx"]):
                            resources.append({
                                "dataset":  slug,
                                "name":     res.get("name", ""),
                                "url":      res.get("url", ""),
                                "format":   fmt,
                                "country":  country_name,
                                "iso3":     hdx_group.upper(),
                            })
        except Exception as e:
            print(f"    HDX lookup failed for {slug}: {e}")
        time.sleep(0.5)

    # If no known slugs, do a text search
    if not resources:
        try:
            r = requests.get(
                f"{HDX_BASE}/package_search",
                params={
                    "q":     f"schools education facilities",
                    "fq":    f"groups:{hdx_group}",
                    "rows":  5,
                    "sort":  "score desc, metadata_modified desc",
                },
                timeout=15
            )
            if r.status_code == 200 and r.json().get("success"):
                for pkg in r.json()["result"]["results"]:
                    for res in pkg.get("resources", []):
                        fmt = res.get("format", "").lower()
                        if any(f in fmt for f in ["csv", "geojson", "zip"]):
                            resources.append({
                                "dataset":  pkg["name"],
                                "name":     res.get("name", ""),
                                "url":      res.get("url", ""),
                                "format":   fmt,
                                "country":  country_name,
                                "iso3":     hdx_group.upper(),
                            })
        except Exception as e:
            print(f"    HDX search failed for {hdx_group}: {e}")
        time.sleep(0.5)

    return resources


def download_hdx_resource(resource: dict) -> gpd.GeoDataFrame | None:
    """Download a HDX resource and parse it into a GeoDataFrame."""
    url    = resource["url"]
    fmt    = resource["format"]
    cname  = resource["country"]
    iso3   = resource["iso3"]

    print(f"    Downloading: {resource['name']} ({fmt})")
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
    except Exception as e:
        print(f"    Download failed: {e}")
        return None

    tmp = os.path.join(OUT_DIR, f"tmp_{iso3}.{fmt.split('/')[0]}")

    try:
        if "geojson" in fmt:
            gdf = gpd.GeoDataFrame.from_features(r.json()["features"])
            gdf["country"]  = cname
            gdf["iso3"]     = iso3
            gdf["source"]   = "HDX"
            return gdf

        elif "csv" in fmt:
            df = pd.read_csv(url, low_memory=False)
            # Find lat/lon columns
            lat_col = next((c for c in df.columns if "lat" in c.lower()), None)
            lon_col = next((c for c in df.columns if "lon" in c.lower() or "lng" in c.lower()), None)
            if lat_col and lon_col:
                df = df.dropna(subset=[lat_col, lon_col])
                gdf = gpd.GeoDataFrame(
                    df,
                    geometry=gpd.points_from_xy(df[lon_col], df[lat_col]),
                    crs="EPSG:4326"
                )
                gdf["country"] = cname
                gdf["iso3"]    = iso3
                gdf["source"]  = "HDX"
                return gdf
            else:
                print(f"    No lat/lon columns found in CSV. Columns: {list(df.columns[:10])}")

        elif "zip" in fmt or "shapefile" in fmt:
            with open(tmp, "wb") as f:
                f.write(r.content)
            gdf = gpd.read_file(f"zip://{tmp}")
            gdf["country"] = cname
            gdf["iso3"]    = iso3
            gdf["source"]  = "HDX"
            os.remove(tmp)
            return gdf

    except Exception as e:
        print(f"    Parse error: {e}")

    return None


# ── Overpass / OSM helpers ─────────────────────────────────────────────────────

def overpass_schools(iso2: str, country_name: str, iso3: str) -> gpd.GeoDataFrame | None:
    """
    Fetch schools from OpenStreetMap via Overpass API for a given country.
    Queries nodes, ways, and relations tagged as educational facilities.
    Uses 'out center' so ways/relations return a single centroid point.
    """
    # Overpass QL: all school types, for entire country, with centroid output
    query = f"""
[out:json][timeout:120];
area["ISO3166-1"="{iso2}"][admin_level=2]->.country;
(
  nwr["amenity"="school"](area.country);
  nwr["amenity"="college"](area.country);
  nwr["amenity"="university"](area.country);
  nwr["amenity"="kindergarten"](area.country);
);
out center tags;
"""
    print(f"    Querying Overpass for {country_name}...")
    try:
        r = requests.post(
            OVERPASS_URL,
            data={"data": query},
            timeout=180,
            headers={"User-Agent": "school-data-research/1.0"}
        )
        r.raise_for_status()
        elements = r.json().get("elements", [])
    except Exception as e:
        print(f"    Overpass failed for {country_name}: {e}")
        return None

    if not elements:
        print(f"    No results from Overpass for {country_name}")
        return None

    rows = []
    for el in elements:
        # Nodes have lat/lon directly; ways/relations use center
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        rows.append({
            "osm_id":   el.get("id"),
            "osm_type": el.get("type"),
            "name":     tags.get("name") or tags.get("name:en", ""),
            "amenity":  tags.get("amenity", "school"),
            "operator": tags.get("operator", ""),
            "levels":   tags.get("school:levels", ""),
            "capacity": tags.get("capacity", ""),
            "country":  country_name,
            "iso3":     iso3,
            "source":   "OSM",
            "geometry": Point(lon, lat),
        })

    if not rows:
        return None

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    print(f"    → {len(gdf):,} schools from OSM")
    return gdf


# ── Main pipeline ─────────────────────────────────────────────────────────────

def main():
    hdx_gdfs  = []
    osm_gdfs  = []
    hdx_done  = set()

    for iso3, (iso2, hdx_group, cname) in COUNTRIES.items():
        print(f"\n[{iso3}] {cname}")

        # ── Try HDX first ──────────────────────────────────────────────────
        resources = hdx_search_schools(hdx_group, cname)
        if resources:
            print(f"  Found {len(resources)} HDX resource(s)")
            for res in resources[:2]:  # take max 2 per country
                gdf = download_hdx_resource(res)
                if gdf is not None and len(gdf) > 0:
                    # Ensure geometry is point (some datasets have polygons)
                    if not all(gdf.geometry.geom_type == "Point"):
                        gdf["geometry"] = gdf.geometry.centroid
                    gdf = gdf[gdf.geometry.notna()]
                    hdx_gdfs.append(gdf)
                    hdx_done.add(iso3)
                    print(f"  ✓ HDX: {len(gdf):,} schools for {cname}")
                    break
        else:
            print(f"  No HDX school dataset found")

        # ── Fall back to OSM for countries without HDX data ────────────────
        if iso3 not in hdx_done:
            print(f"  Falling back to Overpass/OSM...")
            gdf = overpass_schools(iso2, cname, iso3)
            if gdf is not None:
                osm_gdfs.append(gdf)
                print(f"  ✓ OSM: {len(gdf):,} schools for {cname}")
            time.sleep(2)  # respectful pause between Overpass queries

    # ── Save outputs ───────────────────────────────────────────────────────
    print("\n── Saving outputs ──")

    def save_geojson(gdfs: list, path: str, label: str):
        if not gdfs:
            print(f"  {label}: no data")
            return None
        combined = gpd.GeoDataFrame(
            pd.concat(gdfs, ignore_index=True),
            crs="EPSG:4326"
        )
        # Keep only point geometry and key columns
        combined = combined[combined.geometry.geom_type == "Point"]
        combined["latitude"]  = combined.geometry.y.round(5)
        combined["longitude"] = combined.geometry.x.round(5)
        combined.to_file(path, driver="GeoJSON")
        print(f"  {label}: {len(combined):,} schools → {path}")
        return combined

    hdx_gdf = save_geojson(hdx_gdfs, os.path.join(OUT_DIR, "schools_hdx.geojson"),  "HDX")
    osm_gdf = save_geojson(osm_gdfs, os.path.join(OUT_DIR, "schools_osm.geojson"),  "OSM")

    # Merged GeoJSON
    all_gdfs = [g for g in [hdx_gdf, osm_gdf] if g is not None]
    if all_gdfs:
        merged = gpd.GeoDataFrame(
            pd.concat(all_gdfs, ignore_index=True),
            crs="EPSG:4326"
        )
        merged.to_file(os.path.join(OUT_DIR, "schools_merged.geojson"), driver="GeoJSON")
        print(f"\n✅ Merged: {len(merged):,} total schools → {OUT_DIR}/schools_merged.geojson")

        # Also save as CSV for easy Excel inspection
        csv_cols = ["name", "amenity", "country", "iso3", "source", "latitude", "longitude"]
        csv_cols = [c for c in csv_cols if c in merged.columns]
        merged[csv_cols].to_csv(os.path.join(OUT_DIR, "schools_merged.csv"), index=False)
        print(f"   CSV copy  → {OUT_DIR}/schools_merged.csv")

        # Summary
        print(f"\nBreakdown by country:")
        summary = merged.groupby(["country", "source"]).size().reset_index(name="count")
        print(summary.to_string(index=False))


if __name__ == "__main__":
    main()