"""
geocode_admin.py
================
Generic geocoder for any CSV/Excel file containing Country, Admin1, Admin2
columns. Resolves coordinates using the Nominatim (OpenStreetMap) API with:

  • Smart deduplication  — only unique (Admin2, Admin1, Country) combos are
                           geocoded; results are joined back to all rows
  • 3-level fallback     — tries Admin2+Admin1+Country → Admin2+Country →
                           Admin1+Country, so partial data still resolves
  • Cache file           — saves a JSON sidecar of resolved coordinates so
                           re-runs and multi-file workflows never re-query
                           a place already looked up
  • Fuzzy matching       — optionally attempts fuzzy name matching for minor
                           spelling differences (requires `thefuzz` package)
  • Rate limiting        — respects Nominatim ToS (max 1 req/sec)
  • Dry-run mode         — preview what would be queried without hitting the API

Usage (command line)
--------------------
    pip install pandas requests openpyxl thefuzz
    
    # Basic — auto-detects column names, geocodes everything
    python geocode_admin.py MyCountry.csv

    # Specify output path
    python geocode_admin.py MyCountry.csv MyCountry_geocoded.csv

    # Override column names if they differ from Country/Admin1/Admin2
    python geocode_admin.py data.csv --country-col nation --admin1-col state --admin2-col district

    # Dry run — shows what would be queried, no API calls
    python geocode_admin.py data.csv --dry-run

    # Use a shared cache across multiple country files
    python geocode_admin.py Nigeria.csv --cache geocode_cache.json
    python geocode_admin.py Somalia.csv --cache geocode_cache.json

Usage (as a module)
-------------------
    from geocode_admin import geocode_file
    df = geocode_file("Lebanon.csv")
    df = geocode_file("Nigeria.xlsx", admin2_col="LGA", cache_path="cache.json")

Inputs accepted
---------------
    .csv, .xlsx, .xls

Output
------
    Same file with two new columns appended: Latitude, Longitude
    A JSON cache file (default: geocode_cache.json) for reuse

Column name auto-detection
--------------------------
    The script looks for columns matching (case-insensitive):
      Country  → country, nation, iso3, country_name
      Admin1   → admin1, state, governorate, province, region
      Admin2   → admin2, district, county, lga, qadaa, sub-district, zone
    Override with --country-col / --admin1-col / --admin2-col if needed.
"""

import argparse
import json
import os
import sys
import time

import pandas as pd
import requests

# ── Constants ─────────────────────────────────────────────────────────────────
NOMINATIM_URL  = "https://nominatim.openstreetmap.org/search"
USER_AGENT     = "geocode-admin-research/2.0 (open-source field research tool)"
REQUEST_DELAY  = 1.1        # seconds between API calls (Nominatim ToS)
DEFAULT_CACHE  = "utils/geocode_cache.json"
CONSECUTIVE_ERROR_LIMIT = 5 # Stop execution if this many errors occur in a row

# Column name aliases (lowercase) for auto-detection
COUNTRY_ALIASES = ["country", "country_name", "countryname", "nation", "iso3"]
ADMIN1_ALIASES  = ["admin1", "admin1name", "admin_1", "state", "governorate", "province", "region"]
ADMIN2_ALIASES  = ["admin2", "admin2name", "admin_2", "district", "county", "lga", "qadaa",
                   "sub-district", "subdistrict", "zone", "woreda", "upazila", "kecamatan"]

# Global state for error tracking
consecutive_errors = 0

# ── Cache helpers ──────────────────────────────────────────────────────────────
def load_cache(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"  Loaded {len(data)} cached entries from {path}")
            return data
        except Exception as e:
            print(f"  Error loading cache {path}: {e}")
            return {}
    return {}


def save_cache(cache: dict, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def cache_key(admin2: str, admin1: str, country: str) -> str:
    return f"{str(admin2).strip()}|{str(admin1).strip()}|{str(country).strip()}"


# ── Column auto-detection ──────────────────────────────────────────────────────
def detect_col(columns: list[str], aliases: list[str], label: str) -> str | None:
    col_lower = {c.lower().replace(" ", "_"): c for c in columns}
    for alias in aliases:
        if alias in col_lower:
            return col_lower[alias]
    return None


def resolve_columns(df: pd.DataFrame,
                    country_col: str | None,
                    admin1_col:  str | None,
                    admin2_col:  str | None) -> tuple[str, str, str]:
    cols = list(df.columns)

    country_col = country_col or detect_col(cols, COUNTRY_ALIASES, "Country")
    admin1_col  = admin1_col  or detect_col(cols, ADMIN1_ALIASES,  "Admin1")
    admin2_col  = admin2_col  or detect_col(cols, ADMIN2_ALIASES,  "Admin2")

    missing = [label for label, col in
               [("Country", country_col), ("Admin1", admin1_col), ("Admin2", admin2_col)]
               if col is None]
    if missing:
        print(f"\n⚠  Could not auto-detect columns for: {', '.join(missing)}")
        print(f"   Available columns: {cols}")
        print(f"   Use --country-col / --admin1-col / --admin2-col to specify them.\n")
        return None, None, None

    print(f"  Using columns → Country: '{country_col}' | "
          f"Admin1: '{admin1_col}' | Admin2: '{admin2_col}'")
    return country_col, admin1_col, admin2_col


# ── Nominatim geocoder ─────────────────────────────────────────────────────────
def nominatim_query(params: dict) -> tuple[float, float] | None:
    """Single Nominatim request; returns (lat, lon) or None."""
    global consecutive_errors
    try:
        r = requests.get(
            NOMINATIM_URL,
            params={**params, "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        r.raise_for_status()
        consecutive_errors = 0 # Reset on success
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        consecutive_errors += 1
        print(f"    Request error ({consecutive_errors}/{CONSECUTIVE_ERROR_LIMIT}): {e}")
        if consecutive_errors >= CONSECUTIVE_ERROR_LIMIT:
            print(f"❌ Consecutive error limit reached. Stopping to avoid API block.")
            raise RuntimeError("API limit or connection error")
    return None


def geocode_place(admin2: str, admin1: str, country: str) -> tuple[float | None, float | None, str]:
    """
    Try three progressively coarser queries:
      1. Admin2 + Admin1 + Country  (most precise)
      2. Admin2 + Country           (if Admin1 causes ambiguity)
      3. Admin1 + Country           (Admin2 not found, fall back to Admin1 centroid)
    Returns (lat, lon, method_used)
    """
    # Level 1: full structured query
    result = nominatim_query({"county": admin2, "state": admin1, "country": country})
    if result:
        return *result, "admin2+admin1+country"
    time.sleep(REQUEST_DELAY)

    # Level 2: Admin2 + Country only
    result = nominatim_query({"county": admin2, "country": country})
    if result:
        return *result, "admin2+country"
    time.sleep(REQUEST_DELAY)

    # Level 3: free-text fallback with Admin2, Admin1, Country
    result = nominatim_query({"q": f"{admin2}, {admin1}, {country}"})
    if result:
        return *result, "freetext-admin2"
    time.sleep(REQUEST_DELAY)

    # Level 4: fall back to Admin1 centroid
    result = nominatim_query({"state": admin1, "country": country})
    if result:
        return *result, "admin1-fallback"
    time.sleep(REQUEST_DELAY)

    return None, None, "failed"


# ── Fuzzy match helper ─────────────────────────────────────────────────────────
def try_fuzzy_match(name: str, cache: dict, threshold: int = 85) -> tuple[float, float] | None:
    """
    If an exact cache key miss occurs, try fuzzy-matching the Admin2 part
    of existing cache keys. Requires `thefuzz` package.
    """
    try:
        from thefuzz import process
        existing_keys = list(cache.keys())
        if not existing_keys:
            return None
        # Extract just the admin2 portion of cache keys
        admin2_parts = [k.split("|")[0] for k in existing_keys]
        match, score = process.extractOne(name, admin2_parts)
        if score >= threshold:
            matched_key = existing_keys[admin2_parts.index(match)]
            print(f"    Fuzzy matched '{name}' → '{match}' (score {score})")
            return cache[matched_key]
    except ImportError:
        pass
    return None


# ── Core functions ─────────────────────────────────────────────────────────────
def geocode_file(
    input_path:  str,
    output_path: str | None = None,
    country_col: str | None = None,
    admin1_col:  str | None = None,
    admin2_col:  str | None = None,
    cache_path:  str = DEFAULT_CACHE,
    dry_run:     bool = False,
    fuzzy:       bool = True,
) -> bool:
    """
    Main entry point. Reads input_path, geocodes it, saves to output_path.
    Returns True on success, False otherwise.
    """

    # ── Load file ─────────────────────────────────────────────────────────────
    ext = os.path.splitext(input_path)[1].lower()
    try:
        if ext in (".xlsx", ".xls"):
            df = pd.read_excel(input_path)
        elif ext == ".csv":
            df = pd.read_csv(input_path, low_memory=False)
        else:
            print(f"Skipping unsupported file type: {ext}")
            return False
    except Exception as e:
        print(f"Error loading {input_path}: {e}")
        return False

    print(f"\nProcessing: {input_path} ({len(df):,} rows)")

    # ── Detect columns ────────────────────────────────────────────────────────
    c_col, a1_col, a2_col = resolve_columns(df, country_col, admin1_col, admin2_col)
    if not c_col:
        return False

    # ── Identify unique combos to geocode ─────────────────────────────────────
    unique = (
        df[[c_col, a1_col, a2_col]]
        .drop_duplicates()
        .dropna(subset=[a2_col])
        .reset_index(drop=True)
    )

    if dry_run:
        print(f"Dry Run: {len(unique)} unique combos to resolve")
        return True

    # ── Load cache ────────────────────────────────────────────────────────────
    cache = load_cache(cache_path)
    
    # ── Geocode unique combos ─────────────────────────────────────────────────
    results = {}  # key → (lat, lon)

    for i, row in unique.iterrows():
        a2, a1, country = str(row[a2_col]), str(row[a1_col]), str(row[c_col])
        key = cache_key(a2, a1, country)

        if key in cache:
            results[key] = tuple(cache[key])
            continue

        if fuzzy:
            fuzzy_result = try_fuzzy_match(a2, cache)
            if fuzzy_result:
                results[key] = fuzzy_result
                cache[key]   = list(fuzzy_result)
                save_cache(cache, cache_path)
                continue

        # API call
        try:
            lat, lon, method = geocode_place(a2, a1, country)
            time.sleep(REQUEST_DELAY)

            if lat is not None:
                results[key] = (lat, lon)
                cache[key]   = [lat, lon]
                save_cache(cache, cache_path)
                print(f"  [{i+1:>3}/{len(unique)}] {a2:<28} ✓ {method}")
            else:
                results[key] = (None, None)
                print(f"  [{i+1:>3}/{len(unique)}] {a2:<28} ✗ FAILED")
        except RuntimeError:
            # Re-raise to stop the entire batch
            raise

    # ── Join coordinates back to all rows ─────────────────────────────────────
    df["Latitude"]  = df.apply(lambda r: results.get(cache_key(r[a2_col], r[a1_col], r[c_col]), (None, None))[0], axis=1)
    df["Longitude"] = df.apply(lambda r: results.get(cache_key(r[a2_col], r[a1_col], r[c_col]), (None, None))[1], axis=1)

    # ── Save output ───────────────────────────────────────────────────────────
    if output_path is None:
        stem = os.path.splitext(input_path)[0]
        output_path = f"{stem}_geocoded.csv"

    df.to_csv(output_path, index=False)
    print(f"✅ Saved → {output_path}")
    return True


def geocode_directory(
    dir_path:    str,
    country_col: str | None = None,
    admin1_col:  str | None = None,
    admin2_col:  str | None = None,
    cache_path:  str = DEFAULT_CACHE,
    dry_run:     bool = False,
    fuzzy:       bool = True,
):
    """Processes all CSV/Excel files in a directory."""
    files = [f for f in os.listdir(dir_path) if f.lower().endswith(('.csv', '.xlsx', '.xls'))]
    if not files:
        print(f"No valid files found in {dir_path}")
        return

    print(f"\n🚀 Batch processing directory: {dir_path} ({len(files)} files)")
    
    for filename in sorted(files):
        if filename.endswith("_geocoded.csv"): # Skip already geocoded files
            continue
            
        full_path = os.path.join(dir_path, filename)
        try:
            geocode_file(
                input_path  = full_path,
                country_col = country_col,
                admin1_col  = admin1_col,
                admin2_col  = admin2_col,
                cache_path  = cache_path,
                dry_run     = dry_run,
                fuzzy       = fuzzy,
            )
        except RuntimeError:
            print("\nStopping directory processing due to persistent errors.")
            break


# ── CLI ────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Geocode CSV/Excel files or directories with Country, Admin1, Admin2 columns.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python geocode_admin.py Lebanon.csv
  python geocode_admin.py data/raw/acled/split/  (Batch process folder)
  python geocode_admin.py data.csv --admin2-col LGA --admin1-col State
  python geocode_admin.py data.csv --dry-run
        """
    )
    parser.add_argument("input",         help="Input file OR directory path")
    parser.add_argument("output",        nargs="?", default=None,
                                         help="Output CSV path (only for single file input)")
    parser.add_argument("--country-col", default=None, help="Column name for country")
    parser.add_argument("--admin1-col",  default=None, help="Column name for Admin1")
    parser.add_argument("--admin2-col",  default=None, help="Column name for Admin2")
    parser.add_argument("--cache",       default=DEFAULT_CACHE,
                                         help=f"Cache JSON file (default: {DEFAULT_CACHE})")
    parser.add_argument("--dry-run",     action="store_true",
                                         help="Preview queries without calling the API")
    parser.add_argument("--no-fuzzy",    action="store_true",
                                         help="Disable fuzzy name matching")

    args = parser.parse_args()

    if os.path.isdir(args.input):
        geocode_directory(
            dir_path    = args.input,
            country_col = args.country_col,
            admin1_col  = args.admin1_col,
            admin2_col  = args.admin2_col,
            cache_path  = args.cache,
            dry_run     = args.dry_run,
            fuzzy       = not args.no_fuzzy,
        )
    else:
        geocode_file(
            input_path  = args.input,
            output_path = args.output,
            country_col = args.country_col,
            admin1_col  = args.admin1_col,
            admin2_col  = args.admin2_col,
            cache_path  = args.cache,
            dry_run     = args.dry_run,
            fuzzy       = not args.no_fuzzy,
        )


if __name__ == "__main__":
    main()
