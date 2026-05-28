"""
run_all.py
==========
Master Orchestrator — runs the complete 22-step pipeline for any ISO3 country.

Usage:
    python scripts/run_all.py --iso3 MLI
    python scripts/run_all.py --iso3 BFA --only 15 16
    python scripts/run_all.py --iso3 NER --country "Niger"

Environment Variables:
    PIPELINE_ISO3    : The ISO3 code (e.g., BFA)
    PIPELINE_COUNTRY : The common name (e.g., Burkina Faso)
"""

import argparse
import subprocess
import sys
import time
import os
from pathlib import Path

# ── ISO3 to Country Mapping ───────────────────────────────────────────────────
# Add more countries here as needed
COUNTRY_MAP = {
    "BFA": "Burkina Faso",
    "MLI": "Mali",
    "NER": "Niger",
    "SDN": "Sudan",
    "SSD": "South Sudan",
    "SOM": "Somalia",
    "YEM": "Yemen",
    "SYR": "Syria",
    "AFG": "Afghanistan",
    "UKR": "Ukraine",
    "PSE": "Palestine",
    "MMR": "Myanmar",
    "COD": "DR Congo",
    "ETH": "Ethiopia",
    "NGA": "Nigeria",
    "LBN": "Lebanon",
}

# ── Pipeline Steps ───────────────────────────────────────────────────────────
STEPS = {
    # Phase 1: Fetching
    1:  ("01_fetch_acled_hdx.py",              "Fetch ACLED (Global)"),
    2:  ("01_3_fetch_granular_conflicts.py",    "Fetch UCDP Granular Conflicts"),
    3:  ("02_fetch_schools_hdx.py",            "Fetch Schools (HDX)"),
    4:  ("02_fetch_schools.py",                "Fetch Schools (Legacy/OSM)"),
    5:  ("03_fetch_education.py",              "Fetch Education (DHS/WB)"),
    6:  ("03_1_fetch_opri.py",                  "Fetch Education (UNESCO)"),
    7:  ("04_fetch_boundaries.py",              "Fetch Admin Boundaries"),
    8:  ("04_1_fetch_worldpop.py",              "Fetch Population Density"),

    # Phase 2: Processing
    9:  ("01_1_split_acled.py",                "Split ACLED Global Data"),
    10: ("01_2_hrp_country.py",                "Extract Country Conflict Data"),
    11: ("02_x_merge_schools.py",              "Merge School Sources"),
    12: ("02_x_clean_school.py",               "Clean & Deduplicate Schools"),
    13: ("03_x_merge_education.py",            "Merge Education Indicators"),
    14: ("04_x_align_admin_names.py",          "Align Administrative Names"),
    15: ("04_y_validate_data_integrity.py",    "Validate Data Integrity (Quality Gate)"),

    # Phase 3: Analysis
    16: ("05_build_analysis.py",               "Build Baseline Analysis"),
    17: ("05_1_calculate_hybrid_vulnerability.py", "Calculate Hybrid Vulnerability"),
    18: ("06_1_calculate_school_proximity.py", "Calculate School Proximity"),
    19: ("06_2_school_fragility.py",           "Calculate School Fragility"),
    20: ("06_3_calculate_density_gap.py",       "Calculate Density Gap"),
    21: ("06_4_aggregate_at_risk_schools.py",  "Aggregate At-Risk Stats"),

    # Phase 4: Export
    22: ("06_export_map_data.py",              "Export Map Layers"),
    23: ("06_x_export_conflicts_geojson.py",    "Export Conflict GeoJSON"),
    24: ("07_update_dashboard.py",             "Update Dashboard UI (HTML)"),
    25: ("08_merge_field_notes.py",            "Merge Field Observations"),
}


def run_step(script: str, iso3: str, country: str) -> bool:
    """Run a single pipeline step with environment variables."""
    path = Path("scripts") / script
    if not path.exists():
        print(f"  ✗ Script not found: {path}")
        return False

    env = os.environ.copy()
    env["PIPELINE_ISO3"]    = iso3
    env["PIPELINE_COUNTRY"] = country

    # Specific arguments for some scripts that prefer CLI args over ENV
    args = [sys.executable, str(path)]
    if script == "01_2_hrp_country.py":
        args.extend(["--country", country])
    elif script == "02_fetch_schools_hdx.py":
        args.extend(["--countries", iso3])
    elif script == "02_x_merge_schools.py":
        args.append(iso3)
    elif script == "03_fetch_education.py":
        args.append(iso3)
    elif script == "04_fetch_boundaries.py":
        args.append(iso3)
    elif script == "01_3_fetch_granular_conflicts.py":
        args.extend(["--iso3", iso3])
    elif script == "04_1_fetch_worldpop.py":
        args.extend(["--iso3", iso3])
    elif script == "08_merge_field_notes.py":
        # No extra args needed
        pass


    result = subprocess.run(args, env=env)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Master Education Risk Pipeline")
    parser.add_argument("--iso3",    default="BFA",      help="ISO3 country code (default: BFA)")
    parser.add_argument("--country", default=None,       help="Override country name")
    parser.add_argument("--skip",    nargs="*", type=int, default=[], help="Steps to skip")
    parser.add_argument("--only",    nargs="*", type=int, default=None, help="Run ONLY these steps")
    args = parser.parse_args()

    iso3 = args.iso3.upper()
    country = args.country if args.country else COUNTRY_MAP.get(iso3, iso3)
    
    skip_steps = set(args.skip)
    only_steps = set(args.only) if args.only else None

    print(f"\n{'='*60}")
    print(f"  EDUCATION RISK PIPELINE: {country} ({iso3})")
    print(f"{'='*60}\n")

    failed = []
    total_steps = len(STEPS)

    for step_num in sorted(STEPS.keys()):
        script, label = STEPS[step_num]
        
        if step_num in skip_steps:
            print(f"  [{step_num}/{total_steps}] SKIP  {label}")
            continue
        if only_steps and step_num not in only_steps:
            continue

        print(f"  [{step_num}/{total_steps}] START {label}")
        t0 = time.time()
        ok = run_step(script, iso3, country)
        elapsed = time.time() - t0

        if ok:
            print(f"  [{step_num}/{total_steps}] DONE  {label} ({elapsed:.0f}s)\n")
        else:
            print(f"  [{step_num}/{total_steps}] FAIL  {label}\n")
            failed.append(step_num)
            # Critical dependency check: abort if early steps fail
            if step_num < 15:
                print("  ! Aborting — critical step failed. Fix and re-run.")
                sys.exit(1)

    print(f"{'='*60}")
    if failed:
        print(f"  Pipeline finished with failures in steps: {failed}")
    else:
        print(f"  ✅  Pipeline complete for {country} ({iso3})")
        print(f"\n  Outputs available in 'artifacts/' and 'data/clean/'")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
