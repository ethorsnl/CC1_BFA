"""
07_update_dashboard.py
======================
Updates the dashboard HTML files to reflect the target country.
Uses generic regex patterns to identify data "slots" in the HTML,
allowing for repeated updates across different countries without
hardcoded original strings.

Inputs: PIPELINE_ISO3, PIPELINE_COUNTRY
"""

import os
import re
import json
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
ISO3    = os.environ.get("PIPELINE_ISO3", "BFA")
COUNTRY = os.environ.get("PIPELINE_COUNTRY", "Burkina Faso")

# ── Centroid Mapping ─────────────────────────────────────────────────────────
# Helps "frame" the country correctly on load
CENTROIDS = {
    "BFA": {"lat": 12.3,  "lon": -1.9,  "zoom": 7},
    "NGA": {"lat": 9.08,  "lon": 8.68,  "zoom": 6},
    "MLI": {"lat": 17.57, "lon": -4.0,  "zoom": 6},
    "SDN": {"lat": 15.45, "lon": 30.22, "zoom": 6},
    "SSD": {"lat": 6.87,  "lon": 31.30, "zoom": 7},
    "SOM": {"lat": 5.15,  "lon": 46.20, "zoom": 6},
    "YEM": {"lat": 15.55, "lon": 48.52, "zoom": 6},
    "SYR": {"lat": 34.80, "lon": 38.99, "zoom": 7},
    "AFG": {"lat": 33.93, "lon": 67.71, "zoom": 6},
    "UKR": {"lat": 48.37, "lon": 31.16, "zoom": 6},
    "PSE": {"lat": 31.95, "lon": 35.23, "zoom": 9},
    "MMR": {"lat": 21.91, "lon": 95.95, "zoom": 6},
    "COD": {"lat": -4.03, "lon": 21.75, "zoom": 5},
    "ETH": {"lat": 9.14,  "lon": 40.48, "zoom": 6},
    "LBN": {"lat": 33.85, "lon": 35.86, "zoom": 9},
}

def update_file(file_path: Path, patterns: list):
    if not file_path.exists():
        print(f"  ⚠ File not found: {file_path}")
        return

    print(f"  Updating {file_path}...")
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    new_content = content
    for pattern, replacement in patterns:
        new_content = re.sub(pattern, replacement, new_content)

    if new_content != content:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"  ✓ Updated {file_path}")
    else:
        print(f"  - No changes needed for {file_path}")

def main():
    print(f"🚀 Generic Dashboard Update for {ISO3} ({COUNTRY})...")
    
    geo = CENTROIDS.get(ISO3, CENTROIDS["BFA"])
    lat, lon, zoom = geo["lat"], geo["lon"], geo["zoom"]

    # 1. Main Index (index.html)
    # Using generic anchors so it works regardless of which country was there before
    main_patterns = [
        # HTML Title
        (r"<title>Education Under Threat — .*</title>", f"<title>Education Under Threat — {COUNTRY}</title>"),
        # Kicker in Header
        (r"<div class=\"kicker\">\s*.* · Structural & Conflict Risk · 2015–2026\s*</div>", 
         f"<div class=\"kicker\">\n        {COUNTRY} · Structural & Conflict Risk · 2015–2026\n      </div>"),
        # Tooltip/Details Label
        (r"Province: \${name}, .*", f"Province: ${{name}}, {COUNTRY}"),
        # Map Initial View
        (r"map\.setView\(\[[0-9.-]+,\s*[0-9.-]+\],\s*[0-9]+\)", f"map.setView([{lat}, {lon}], {zoom})"),
        # Reset View Coordinates
        (r"map\.setView\(\[12\.3,\s*-1\.9\],\s*7\)", f"map.setView([{lat}, {lon}], {zoom})"),
    ]
    update_file(Path("index.html"), main_patterns)

    # 2. Schools Index (schools/index.html)
    schools_patterns = [
        # Kicker
        (r"<div class=\"kicker\">\s*.* · Education Infrastructure · 2015–2026\s*</div>",
         f"<div class=\"kicker\">\n        {COUNTRY} · Education Infrastructure · 2015–2026\n      </div>"),
        # Subhead
        (r"Detailed analysis of .* schools", f"Detailed analysis of {COUNTRY} schools"),
        # CSV Data Source (e.g. BFA_school_vulnerability.csv -> NGA_school_vulnerability.csv)
        (r"[A-Z]{3}_school_vulnerability\.csv", f"{ISO3}_school_vulnerability.csv"),
        # Map Initial View
        (r"map\.setView\(\[[0-9.-]+,\s*[0-9.-]+\],\s*[0-9]+\)", f"map.setView([{lat}, {lon}], {zoom})"),
    ]
    update_file(Path("schools/index.html"), schools_patterns)

    # 3. At Risk Summary (schools/at_risk_summary.html)
    summary_patterns = [
        # HTML Title
        (r"<title>At-Risk Schools Summary: .*</title>", f"<title>At-Risk Schools Summary: {COUNTRY}</title>"),
        # H1
        (r"<h1>At-Risk Schools Summary: .*</h1>", f"<h1>At-Risk Schools Summary: <span id=\"yearVal\"></span></h1>"),
        # CSV Data Source
        (r"[A-Z]{3}_school_vulnerability\.csv", f"{ISO3}_school_vulnerability.csv"),
    ]
    update_file(Path("schools/at_risk_summary.html"), summary_patterns)

    print("✅ Dashboard update complete.")

if __name__ == "__main__":
    main()
