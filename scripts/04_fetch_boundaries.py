"""
03_fetch_boundaries.py
======================
Downloads admin1 + admin2 boundaries from OCHA HDX for target countries.
Used to build the choropleth map layer.

Output: data/raw/boundaries/{ISO3}_admin1.geojson
        data/raw/boundaries/{ISO3}_admin2.geojson
"""

import requests, json, time
from pathlib import Path

import requests, json, time, argparse, sys
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
OUT_DIR = Path("data/raw/boundaries")
OUT_DIR.mkdir(parents=True, exist_ok=True)

HDX_API  = "https://data.humdata.org/api/3/action"
HEADERS  = {"User-Agent": "education-risk-research/1.0"}

# Known COD boundary dataset slugs for admin boundaries
# Pattern: cod-ab-{iso3}
COD_SLUG_PATTERN = "cod-ab-{iso3}"


def find_boundary_resources(iso3: str) -> list[dict]:
    """Search HDX for COD admin boundary resources for a country."""
    slug = COD_SLUG_PATTERN.format(iso3=iso3.lower())
    r = requests.get(
        f"{HDX_API}/package_show",
        params={"id": slug},
        headers=HEADERS,
        timeout=15,
    )
    if r.status_code != 200:
        return []
    data = r.json()
    if not data.get("success"):
        return []
    return data["result"].get("resources", [])


def download_and_extract(url: str, iso3: str) -> None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=120)
        r.raise_for_status()
        
        # Handle ZIP
        if "zip" in url.lower() or r.headers.get("Content-Type", "").startswith("application/zip"):
            import io, zipfile
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                for member in z.namelist():
                    name_low = member.lower()
                    if "admin1" in name_low and (name_low.endswith(".geojson") or name_low.endswith(".json")):
                        out = OUT_DIR / f"{iso3}_admin1.geojson"
                        with z.open(member) as f_in, open(out, "wb") as f_out:
                            f_out.write(f_in.read())
                        print(f"  Admin1: ✓ → {out}")
                    
                    elif "admin2" in name_low and (name_low.endswith(".geojson") or name_low.endswith(".json")):
                        out = OUT_DIR / f"{iso3}_admin2.geojson"
                        with z.open(member) as f_in, open(out, "wb") as f_out:
                            f_out.write(f_in.read())
                        print(f"  Admin2: ✓ → {out}")
        else:
            # Single file download (requires identifying which level it is)
            name_low = url.lower()
            level = "admin1" if "admin1" in name_low else "admin2" if "admin2" in name_low else "unknown"
            if level != "unknown":
                out = OUT_DIR / f"{iso3}_{level}.geojson"
                with open(out, "wb") as f:
                    f.write(r.content)
                print(f"  {level.capitalize()}: ✓ → {out}")
                
    except Exception as e:
        print(f"    Error during download/extract: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Admin boundaries from HDX")
    parser.add_argument("iso3", nargs="*", help="ISO3 codes to fetch (e.g., BFA MLI)")
    args = parser.parse_args()

    target_isos = args.iso3 if args.iso3 else ["BFA"]

    for iso3 in target_isos:
        iso3 = iso3.upper()
        print(f"\n[{iso3}] Fetching boundaries...")
        resources = find_boundary_resources(iso3)

        if not resources:
            print(f"  ⚠ No COD dataset found for {iso3}")
            print(f"    Manual check: https://data.humdata.org/dataset/cod-ab-{iso3.lower()}")
            continue

        # Look for GeoJSON resource (could be a zip)
        geojson_res = next((res for res in resources if "geojson" in res.get("format", "").lower() or "geojson" in res.get("url", "").lower()), None)
        
        if geojson_res:
            download_and_extract(geojson_res["url"], iso3)
        else:
            print(f"  ⚠ No GeoJSON resource found for {iso3}")

        time.sleep(0.5)

    print("\n✓ Boundary fetch complete")
