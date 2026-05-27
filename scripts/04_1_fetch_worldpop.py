"""
worldpop_fetch.py
=================
Fetches WorldPop "Global per country 2000-2020" population density GeoTIFFs,
downsamples them for heatmaps, and optionally aggregates population to
administrative boundaries (provinces).

Data source : WorldPop (www.worldpop.org) — CC-BY 4.0
API root    : https://hub.worldpop.org/rest/data/pop/wpgp?iso3={ISO3}

Usage
-----
    pip install requests rasterio numpy tqdm rasterstats pandas
    python scripts/04_1_fetch_worldpop.py --iso3 BFA --years latest --boundary data/raw/boundaries/BFA_admin2.geojson

Output
------
    data/clean/{iso3}_pop_density/
        {iso3}_pop_2020.json    ← heatmap data
        {iso3}_zonal_2020.csv   ← province aggregation
        {iso3}_index.json       ← manifest
"""

import os
import json
import math
import time
import requests
import numpy as np
import rasterio
import argparse
from typing import Optional
from rasterio.enums import Resampling
from tqdm import tqdm

# Optional dependencies for zonal stats
try:
    import pandas as pd
    from rasterstats import zonal_stats
    HAS_ZONAL = True
except ImportError:
    HAS_ZONAL = False

# ── Configuration ──────────────────────────────────────────────────────────────

# Default years range for 'all'
ALL_YEARS = list(range(2000, 2021))
LATEST_YEAR = 2020

# Downsample factor — 50 means 50×50 native pixels → 1 output pixel
# Native resolution ≈ 100 m → factor 50 ≈ 5 km output grid
DOWNSAMPLE_FACTOR = 50

# Minimum population per output pixel to include (filters out near-zero cells)
MIN_POP = 1.0

# WorldPop API + FTP mirror
API_BASE  = "https://hub.worldpop.org/rest/data/pop/wpgp"
FTP_TPL   = "https://data.worldpop.org/GIS/Population/Global_2000_2020/{year}/{iso3}/{iso3_lower}_ppp_{year}.tif"


# ── Step 1: Resolve download URLs via WorldPop API ─────────────────────────────

def get_worldpop_url(iso3: str, year: int) -> Optional[str]:
    """Query WorldPop API for the download URL."""
    try:
        resp = requests.get(API_BASE, params={"iso3": iso3}, timeout=15)
        resp.raise_for_status()
        records = resp.json().get("data", [])
        for rec in records:
            if str(rec.get("popyear")) == str(year) or str(year) in str(rec.get("title", "")):
                files = rec.get("files", [])
                if files:
                    return files[0].replace("ftp://ftp.worldpop.org.uk", "https://data.worldpop.org")
    except Exception as e:
        print(f"  API lookup failed for {iso3}/{year}: {e} — using mirror URL")

    return FTP_TPL.format(year=year, iso3=iso3, iso3_lower=iso3.lower())


# ── Step 2: Download TIF ───────────────────────────────────────────────────────

def download_tif(url: str, dest_path: str) -> bool:
    """Download a file with progress bar."""
    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100_000:
        return True  # already cached

    for attempt_url in [url, url.replace(".tif", "_UNadj.tif")]:
        try:
            with requests.get(attempt_url, stream=True, timeout=60) as r:
                if r.status_code == 404: continue
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                with open(dest_path, "wb") as f, tqdm(
                    desc=f"  ↓ {os.path.basename(dest_path)}",
                    total=total, unit="B", unit_scale=True, leave=False
                ) as bar:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
                        bar.update(len(chunk))
            return True
        except Exception as e:
            print(f"    Warning: {attempt_url} → {e}")

    print(f"  ✗ Could not download {os.path.basename(dest_path)}")
    return False


# ── Step 3: Extract point array (Heatmap) ──────────────────────────────────────

def tif_to_points(tif_path: str, factor: int = DOWNSAMPLE_FACTOR) -> list[list]:
    """Read TIF and downsample to a list of [lat, lon, pop]."""
    points = []
    try:
        with rasterio.open(tif_path) as src:
            new_h = max(1, src.height // factor)
            new_w = max(1, src.width  // factor)
            data = src.read(1, out_shape=(new_h, new_w), resampling=Resampling.average)

            res_lon = abs(src.transform.a) * factor
            res_lat = abs(src.transform.e) * factor
            mid_lat  = (src.bounds.top + src.bounds.bottom) / 2
            pixel_area_km2 = res_lon * 111.32 * math.cos(math.radians(mid_lat)) * res_lat * 111.32
            transform = src.transform * src.transform.scale(src.width / new_w, src.height / new_h)
            nodata = src.nodata if src.nodata is not None else -99999

            for row_i in range(new_h):
                for col_i in range(new_w):
                    avg_val = data[row_i, col_i]
                    if avg_val is None or avg_val == nodata or np.isnan(avg_val): continue
                    val = avg_val * (factor * factor)
                    if val < MIN_POP: continue
                    lon, lat = rasterio.transform.xy(transform, row_i, col_i, offset="center")
                    pop_km2 = round(float(val) / pixel_area_km2, 1)
                    points.append([round(lat, 4), round(lon, 4), pop_km2])
    except Exception as e:
        print(f"  ✗ Error processing {tif_path}: {e}")
    return points


# ── Step 4: Zonal Statistics (Province Level) ──────────────────────────────────

def run_zonal_stats(tif_path: str, boundary_path: str, output_path: str) -> bool:
    """Aggregates population to administrative boundaries."""
    if not HAS_ZONAL:
        print("  ⚠️ Skipping zonal stats: 'rasterstats' or 'pandas' not installed.")
        return False

    print(f"  Aggregating to boundaries: {os.path.basename(boundary_path)}...")
    try:
        stats = zonal_stats(
            boundary_path,
            tif_path,
            stats=["sum"],
            nodata=-99999,
            geojson_out=True
        )
        
        records = []
        for feat in stats:
            props = feat["properties"]
            name_col = next((k for k in props if any(x in k.lower() for x in ["adm2_en", "adm1_en", "adm2name", "admin2", "nom", "name"])), "ID")
            records.append({
                "Region": props.get(name_col, "Unknown"),
                "Population": round(props.get("sum", 0) or 0, 0),
            })
        
        df = pd.DataFrame(records)
        df.to_csv(output_path, index=False)
        print(f"  ✓ Saved zonal stats to {os.path.basename(output_path)}")
        return True
        
    except Exception as e:
        print(f"  ✗ Error in zonal stats: {e}")
        return False


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch and process WorldPop data.")
    parser.add_argument("--iso3", default="BFA", help="ISO3 country code (default: BFA)")
    parser.add_argument("--years", default="latest", help="Years: 'latest', 'all', or comma-separated list")
    parser.add_argument("--boundary", help="Path to GeoJSON for zonal aggregation (e.g. province-level)")
    parser.add_argument("--clean", action="store_true", help="Delete TIF files after processing to save space")
    parser.add_argument("--yes", action="store_true", help="Skip the large download warning prompt")
    
    args = parser.parse_args()
    iso3 = args.iso3.upper()
    iso3_lower = iso3.lower()
    
    # Define paths
    tif_dir = f"data/raw/{iso3_lower}_pd_tifs"
    out_dir = f"data/clean/{iso3_lower}_pop_density"
    os.makedirs(tif_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    if args.years.lower() == "all": years = ALL_YEARS
    elif args.years.lower() == "latest": years = [LATEST_YEAR]
    else:
        try: years = [int(y.strip()) for y in args.years.split(",")]
        except ValueError:
            print(f"Error: Invalid years format '{args.years}'.")
            return

    # ── Large Download Warning ────────────────────────────────────────────────
    if len(years) > 1 and not args.yes:
        print("\n" + "!"*60)
        print(f"⚠️  WARNING: LARGE DOWNLOAD DETECTED ({len(years)} years)")
        print(f"Each WorldPop TIF file is typically 50MB-200MB.")
        print(f"Estimated total download: {len(years) * 100}MB+")
        print("This may take several minutes and significant disk space.")
        print("!"*60 + "\n")
        
        try:
            confirm = input(f"Proceed with downloading {len(years)} files? (y/N): ")
            if confirm.lower() != 'y':
                print("Aborted by user.")
                return
        except EOFError:
            # Fallback for non-interactive environments (like CI or background runs)
            print("Non-interactive environment detected. Continuing...")

    print(f"WorldPop fetcher | Country: {iso3} | Years: {years}\n")

    index = {
        "years": years, "country": iso3, "files": {}, "zonal_files": {},
        "description": "Population data (heatmap JSON + zonal CSV)",
        "source": "WorldPop Global per country 2000-2020 (CC-BY 4.0)"
    }

    for year in years:
        print(f"\n Year: {year} " + "="*50)
        tif_name = f"{iso3_lower}_ppp_{year}.tif"
        tif_path = os.path.join(tif_dir, tif_name)

        url = get_worldpop_url(iso3, year)
        if not download_tif(url, tif_path): continue

        # Heatmap
        pts = tif_to_points(tif_path)
        out_name = f"{iso3_lower}_pop_{year}.json"
        out_path = os.path.join(out_dir, out_name)
        with open(out_path, "w") as f:
            json.dump({"year": year, "count": len(pts), "unit": "people_per_km2", "data": pts}, f, separators=(",", ":"))
        print(f"  ✓ Saved heatmap to {out_name} ({len(pts):,} cells)")
        index["files"][str(year)] = out_name

        # Zonal Stats
        if args.boundary:
            zonal_name = f"{iso3_lower}_zonal_{year}.csv"
            zonal_path = os.path.join(out_dir, zonal_name)
            if run_zonal_stats(tif_path, args.boundary, zonal_path):
                index["zonal_files"][str(year)] = zonal_name

        if args.clean:
            try:
                os.remove(tif_path)
                print(f"  🗑 Removed {tif_name}")
            except Exception as e:
                print(f"  Warning: Could not remove {tif_path}: {e}")

    # Write manifest (Merging with existing if it exists)
    manifest_path = os.path.join(out_dir, f"{iso3_lower}_index.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r") as f:
                existing_index = json.load(f)
            # Merge years and files
            index["years"] = sorted(list(set(index["years"] + existing_index.get("years", []))))
            existing_files = existing_index.get("files", {})
            existing_files.update(index["files"])
            index["files"] = existing_files
            
            existing_zonal = existing_index.get("zonal_files", {})
            existing_zonal.update(index["zonal_files"])
            index["zonal_files"] = existing_zonal
        except Exception as e:
            print(f"  Warning: Could not merge with existing index: {e}")

    with open(manifest_path, "w") as f:
        json.dump(index, f, indent=2)

    print(f"\n✅ Done. Outputs in ./{out_dir}/")

if __name__ == "__main__":
    main()
