"""
fetch_ucdp_granular.py
======================
Fetches individual conflict events (with lat/lon) from the UCDP GED API
for any country, resolved dynamically — no hardcoded country code dict.

UCDP GED (Georeferenced Event Dataset) covers 1989–present at event level.
Each row is one conflict event with:
  id, year, date_start, date_end, country, adm_1, adm_2,
  latitude, longitude, best (best fatality estimate),
  low, high (fatality range), type_of_violence, conflict_name

Usage:
    python fetch_ucdp_granular.py --iso3 BFA
    python fetch_ucdp_granular.py --iso3 NGA --start 2015 --end 2024
    python fetch_ucdp_granular.py --iso3 YEM --version 24.1

Country code resolution:
    Queries the UCDP /countries/ endpoint on first run and caches the
    ISO3→UCDP_ID mapping to data/raw/conflicts/ucdp_country_codes.json.
    Subsequent runs use the cache — no extra API call needed.

Install: pip install requests pandas
"""

import argparse
import json
import os
import time
import requests
import pandas as pd
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
UCDP_BASE    = "https://ucdpapi.prio.org/api"
UCDP_VERSION = "24.1"
PAGE_SIZE    = 1000
MAX_PAGES    = 200     # safety cap: 200 × 1000 = 200k events max
HEADERS      = {"User-Agent": "education-conflict-research/1.0"}

RAW_DIR      = Path("data/raw/conflicts")
CACHE_FILE   = RAW_DIR / "ucdp_country_codes.json"
RAW_DIR.mkdir(parents=True, exist_ok=True)



# ── Bulk download mode ────────────────────────────────────────────────────────

# Latest confirmed bulk CSV URL (GED 25.1, covers 1989-2024)
# Check https://ucdp.uu.se/downloads/ for newer versions
BULK_URLS = [
    "https://ucdp.uu.se/downloads/ged/ged251-csv.zip",   # GED 25.1 (current)
    "https://ucdp.uu.se/downloads/ged/ged241-csv.zip",   # GED 24.1 (fallback)
]
BULK_CACHE = RAW_DIR / "ucdp_ged_global.csv"   # cached full CSV if --keep-bulk


def download_bulk_csv(keep: bool = False) -> Path | None:
    """
    Download the UCDP GED global bulk CSV ZIP, extract to temp file.
    Returns path to the CSV, or None on failure.
    If keep=True, saves permanently to BULK_CACHE instead of a temp file.
    """
    import io, zipfile, tempfile

    for url in BULK_URLS:
        print(f"  Downloading UCDP GED bulk CSV...")
        print(f"  {url}  (~150MB)")
        try:
            r = requests.get(url, stream=True, headers=HEADERS, timeout=300)
            if r.status_code == 404:
                print(f"  404 — trying next URL...")
                continue
            r.raise_for_status()

            total = int(r.headers.get("content-length", 0))
            buf   = io.BytesIO()
            done  = 0
            for chunk in r.iter_content(1 << 20):
                buf.write(chunk)
                done += len(chunk)
                if total:
                    print(f"  {done/1e6:.0f}/{total/1e6:.0f} MB", end="\r")
            print(f"  Downloaded {done/1e6:.0f} MB        ")

            # Extract CSV from ZIP
            buf.seek(0)
            with zipfile.ZipFile(buf) as zf:
                csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
                if not csv_names:
                    print(f"  No CSV found in ZIP: {zf.namelist()}")
                    continue
                csv_name = csv_names[0]
                print(f"  Extracting: {csv_name}")

                if keep:
                    out_path = BULK_CACHE
                    with zf.open(csv_name) as src, open(out_path, "wb") as dst:
                        dst.write(src.read())
                    return out_path
                else:
                    # Write to temp file — caller must delete
                    tmp = tempfile.NamedTemporaryFile(
                        suffix=".csv", delete=False, dir=RAW_DIR
                    )
                    with zf.open(csv_name) as src:
                        tmp.write(src.read())
                    tmp.flush()
                    return Path(tmp.name)

        except Exception as e:
            print(f"  Error: {e}")
            continue

    return None


def filter_bulk_csv(csv_path: Path, country_name: str,
                    start_year: int, end_year: int) -> pd.DataFrame:
    """
    Stream-filter the large UCDP GED CSV to just the country + year range.
    Uses chunked reading to avoid loading 150MB into memory at once.
    Country matched against the 'country' column (name-based, no numeric ID).
    """
    print(f"  Filtering to: {country_name}  ({start_year}–{end_year})")
    kept  = []
    total = 0
    name_lower = country_name.lower()

    for chunk in pd.read_csv(csv_path, chunksize=50_000, low_memory=False,
                              encoding="utf-8"):
        total += len(chunk)

        # Match country name — case-insensitive, partial match for variants
        # e.g. "Congo, DR" matches "Democratic Republic of the Congo"
        if "country" in chunk.columns:
            mask = chunk["country"].str.lower().str.contains(
                name_lower.split(",")[0].strip(),  # use first word of name
                na=False, regex=False
            )
            chunk = chunk[mask]

        # Year filter
        if "year" in chunk.columns:
            chunk = chunk[pd.to_numeric(chunk["year"], errors="coerce")
                            .between(start_year, end_year)]

        if not chunk.empty:
            kept.append(chunk)

        print(f"  Scanned {total:>7,} rows | kept {sum(len(k) for k in kept):>5,}",
              end="\r")

    print()
    if not kept:
        return pd.DataFrame()
    return pd.concat(kept, ignore_index=True)


# ── Country code resolution (API mode only) ─────────────────────────────────

def load_code_cache() -> dict:
    """Load cached ISO3 → UCDP country ID mapping."""
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_code_cache(cache: dict):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def fetch_ucdp_country_list(version: str) -> list[dict]:
    """
    Fetch the full UCDP country list from the /countries/ endpoint.
    Returns list of dicts with keys: id, name, gwno (Gleditsch-Ward number).

    UCDP uses its own numeric IDs, not ISO3 — this endpoint is the bridge.
    """
    url     = f"{UCDP_BASE}/countries/{version}"
    results = []
    page    = 0

    while True:
        r = requests.get(
            url,
            params={"pagesize": PAGE_SIZE, "pagenum": page},
            headers=HEADERS,
            timeout=30,
        )
        r.raise_for_status()
        data   = r.json()
        batch  = data.get("Result", [])
        if not batch:
            break
        results.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        page += 1
        time.sleep(0.3)

    return results


def resolve_country_code(iso3: str, version: str) -> int | None:
    """
    Resolve ISO3 country code to UCDP numeric ID.

    Strategy:
    1. Check local cache
    2. If not cached, fetch full UCDP country list and match by name
       using pycountry to convert ISO3 → country name for matching
    3. Cache the result

    Returns UCDP integer ID, or None if not found.
    """
    cache = load_code_cache()

    if iso3 in cache:
        print(f"  Country code (cached): {iso3} → {cache[iso3]}")
        return cache[iso3]

    # Resolve ISO3 → country name using pycountry
    country_name = iso3_to_name(iso3)
    if not country_name:
        print(f"  ✗ Could not resolve ISO3 '{iso3}' to a country name")
        return None

    print(f"  Fetching UCDP country list to resolve '{iso3}' ({country_name})...")
    try:
        countries = fetch_ucdp_country_list(version)
    except Exception as e:
        print(f"  ✗ Failed to fetch country list: {e}")
        return None

    # Match UCDP country name against resolved name
    # UCDP uses its own name variants — try exact, then partial
    ucdp_id = None
    name_lower = country_name.lower()

    for c in countries:
        ucdp_name = c.get("name", "").lower()
        if ucdp_name == name_lower:
            ucdp_id = c["id"]
            break

    # Partial match fallback (handles "Congo, DR" vs "Democratic Republic of the Congo" etc.)
    if ucdp_id is None:
        for c in countries:
            ucdp_name = c.get("name", "").lower()
            # Check if either name contains the other
            if name_lower in ucdp_name or ucdp_name in name_lower:
                ucdp_id = c["id"]
                print(f"  Partial match: '{country_name}' ↔ '{c['name']}' (id={ucdp_id})")
                break

    if ucdp_id is None:
        # Print available names to help user debug
        print(f"  ✗ No UCDP match found for '{country_name}'")
        print(f"  Available UCDP country names (sample):")
        for c in sorted(countries, key=lambda x: x.get("name",""))[:20]:
            print(f"    id={c['id']:>4}  {c['name']}")
        print(f"  Use --ucdp-id to override manually")
        return None

    # Cache and return
    cache[iso3] = ucdp_id
    save_code_cache(cache)
    print(f"  Resolved: {iso3} ({country_name}) → UCDP id {ucdp_id}")
    return ucdp_id


def iso3_to_name(iso3: str) -> str | None:
    """
    Convert ISO3 code to English country name.
    Uses pycountry if available, falls back to a small built-in dict.
    """
    try:
        import pycountry
        country = pycountry.countries.get(alpha_3=iso3.upper())
        if country:
            return country.name
    except ImportError:
        pass

    # Fallback dict for common conflict-zone countries
    # (covers the case where pycountry isn't installed)
    FALLBACK = {
        "BFA": "Burkina Faso",
        "NGA": "Nigeria",
        "MLI": "Mali",
        "NER": "Niger",
        "TCD": "Chad",
        "CAF": "Central African Republic",
        "COD": "DR Congo of the Congo",
        "SOM": "Somalia",
        "ETH": "Ethiopia",
        "SDN": "Sudan",
        "SSD": "South Sudan",
        "MOZ": "Mozambique",
        "YEM": "Yemen",
        "SYR": "Syria",
        "IRQ": "Iraq",
        "AFG": "Afghanistan",
        "PSE": "Palestine",
        "LBN": "Lebanon",
        "LBY": "Libya",
        "MMR": "Myanmar",
        "PAK": "Pakistan",
        "BGD": "Bangladesh",
        "KHM": "Cambodia",
        "PHL": "Philippines",
        "UKR": "Ukraine",
        "HTI": "Haiti",
    }
    name = FALLBACK.get(iso3.upper())
    if not name:
        print(f"  ⚠ pycountry not installed and '{iso3}' not in fallback dict")
        print(f"  Install pycountry: pip install pycountry")
        print(f"  Or use --ucdp-id to specify the code manually")
    return name


# ── Fetch events ──────────────────────────────────────────────────────────────

def fetch_events(ucdp_id: int, version: str,
                 start_year: int, end_year: int) -> pd.DataFrame:
    """
    Fetch all GED events for a country within the year range.
    Paginates automatically. Returns a DataFrame.

    UCDP API filter parameters:
      country    = UCDP numeric country ID
      StartDate  = YYYY-01-01
      EndDate    = YYYY-12-31
    """
    url        = f"{UCDP_BASE}/gedevents/{version}"
    all_events = []
    page       = 0

    print(f"  Fetching events (UCDP id={ucdp_id}, {start_year}–{end_year})...")

    while page < MAX_PAGES:
        params = {
            "pagesize":  PAGE_SIZE,
            "pagenum":   page,
            "country":   ucdp_id,
            "StartDate": f"{start_year}-01-01",
            "EndDate":   f"{end_year}-12-31",
        }
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            data   = r.json()
            batch  = data.get("Result", [])
        except Exception as e:
            print(f"  ✗ Error on page {page}: {e}")
            break

        if not batch:
            break

        all_events.extend(batch)
        print(f"  Page {page+1}: +{len(batch)} events  "
              f"(total: {len(all_events)})", end="\r")

        if len(batch) < PAGE_SIZE:
            break   # last page

        page += 1
        time.sleep(0.3)   # polite pacing

    print()   # newline after \r

    if not all_events:
        return pd.DataFrame()

    df = pd.DataFrame(all_events)

    # Filter year range client-side as well (API date filter can be approximate)
    if "year" in df.columns:
        df["year"] = pd.to_numeric(df["year"], errors="coerce")
        df = df[df["year"].between(start_year, end_year)]

    return df


def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Standardise column names and types.
    UCDP GED key columns:
      id, year, date_start, date_end,
      country, country_id,
      adm_1, adm_2, adm_3,
      latitude, longitude,
      best, low, high,        ← fatality estimates
      type_of_violence,       ← 1=state, 2=non-state, 3=one-sided
      conflict_name, dyad_name
    """
    numeric_cols = ["year", "latitude", "longitude", "best", "low", "high"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop rows with no coordinates
    if "latitude" in df.columns and "longitude" in df.columns:
        before = len(df)
        df = df.dropna(subset=["latitude", "longitude"])
        dropped = before - len(df)
        if dropped:
            print(f"  Dropped {dropped} rows with missing coordinates")

    # Normalise violence type to a readable label
    if "type_of_violence" in df.columns:
        violence_map = {1: "state_based", 2: "non_state", 3: "one_sided"}
        df["violence_type"] = df["type_of_violence"].map(violence_map)

    return df


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch individual UCDP conflict events with lat/lon for any country.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python fetch_ucdp_granular.py --iso3 BFA
  python fetch_ucdp_granular.py --iso3 NGA --start 2015 --end 2024
  python fetch_ucdp_granular.py --iso3 YEM --version 24.1
  python fetch_ucdp_granular.py --iso3 COD --ucdp-id 490  # manual override
  python fetch_ucdp_granular.py --iso3 BFA --clear-cache  # re-resolve codes
        """
    )
    parser.add_argument("--iso3",        required=True,
                        help="ISO3 country code (e.g. BFA, NGA, YEM)")
    parser.add_argument("--start",       type=int, default=2015,
                        help="Start year (default: 2015)")
    parser.add_argument("--end",         type=int, default=2026,
                        help="End year (default: 2026)")
    parser.add_argument("--version",     default=UCDP_VERSION,
                        help=f"UCDP API version (default: {UCDP_VERSION})")
    parser.add_argument("--mode",        choices=["bulk","api"], default="bulk",
                        help="bulk=download full CSV (default), api=use REST API")
    parser.add_argument("--keep-bulk",   action="store_true",
                        help="Keep the full GED CSV after filtering (150MB)")
    parser.add_argument("--ucdp-id",     type=int, default=None,
                        help="Override UCDP country ID (skip auto-resolution)")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Delete cached country codes and re-fetch")
    parser.add_argument("--out",         default=None,
                        help="Output CSV path (default: data/raw/conflicts/{ISO3}_granular_conflicts.csv)")
    args = parser.parse_args()

    iso3 = args.iso3.upper()

    # Clear cache if requested
    if args.clear_cache and CACHE_FILE.exists():
        CACHE_FILE.unlink()
        print("  Cache cleared")

    out_path = Path(args.out) if args.out else \
               RAW_DIR / f"{iso3}_granular_conflicts.csv"

    print(f"\nUCDP GED fetch — {iso3}  ({args.start}–{args.end})  mode={args.mode}\n")

    # Resolve country name (needed for both bulk and API modes)
    country_name = iso3_to_name(iso3)
    if not country_name:
        print(f"✗ Cannot resolve ISO3 '{iso3}' to a country name")
        print(f"  Install pycountry: pip install pycountry")
        raise SystemExit(1)

    # ── BULK MODE (default) ───────────────────────────────────────────────────
    if args.mode == "bulk":
        # Use cached full CSV if available
        if BULK_CACHE.exists() and not args.clear_cache:
            print(f"  Using cached bulk CSV: {BULK_CACHE}")
            csv_path = BULK_CACHE
            cleanup  = False
        else:
            csv_path = download_bulk_csv(keep=args.keep_bulk)
            cleanup  = not args.keep_bulk
            if csv_path is None:
                print(f"\n✗ Bulk download failed.")
                print(f"  Try API mode: python fetch_ucdp_granular.py --iso3 {iso3} --mode api")
                print(f"  Or download manually from: https://ucdp.uu.se/downloads/")
                print(f"  Save as: {BULK_CACHE}")
                raise SystemExit(1)

        df = filter_bulk_csv(csv_path, country_name, args.start, args.end)

        if cleanup:
            import os as _os
            _os.unlink(csv_path)
            print(f"  Temp CSV deleted")

    # ── API MODE ──────────────────────────────────────────────────────────────
    else:
        if args.ucdp_id:
            ucdp_id = args.ucdp_id
            cache   = load_code_cache()
            cache[iso3] = ucdp_id
            save_code_cache(cache)
        else:
            ucdp_id = resolve_country_code(iso3, args.version)
            if ucdp_id is None:
                print(f"\n✗ Cannot resolve UCDP country ID for {iso3}")
                print(f"  Try bulk mode instead: python fetch_ucdp_granular.py --iso3 {iso3}")
                raise SystemExit(1)
        df = fetch_events(ucdp_id, args.version, args.start, args.end)

    if df.empty:
        print(f"  ⚠ No events returned for {iso3}")
        # Write empty file so downstream scripts don't crash
        pd.DataFrame(columns=[
            "id","year","date_start","date_end","country","adm_1","adm_2",
            "latitude","longitude","best","low","high","type_of_violence",
            "conflict_name"
        ]).to_csv(out_path, index=False)
        print(f"  Empty file written → {out_path}")
        return

    # Clean
    df = clean_columns(df)

    # Save
    df.to_csv(out_path, index=False)

    print(f"\n✅  {len(df):,} events → {out_path}")
    print(f"   Years:   {int(df['year'].min())} – {int(df['year'].max())}")
    print(f"   Columns: {list(df.columns)}")
    if "best" in df.columns:
        print(f"   Total fatalities (best estimate): {df['best'].sum():,.0f}")
    if "violence_type" in df.columns:
        print(f"\n   By violence type:")
        print(df["violence_type"].value_counts().to_string())


if __name__ == "__main__":
    main()