"""
06_4_aggregate_at_risk_schools.py
================================
Aggregates individual school risk scores into province-level annual summaries.
Used by the interactive map to show time-series trends and markers.

Dynamic Version: Calculates risk for EACH year based on the province's score in that year.
"""

import json
import os
import pandas as pd
import numpy as np
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
ISO3    = os.environ.get("PIPELINE_ISO3", "BFA")
COUNTRY = os.environ.get("PIPELINE_COUNTRY", "Burkina Faso")

def aggregate_at_risk_schools():
    print(f"🚀 Aggregating dynamic at-risk school statistics for {ISO3} ({COUNTRY})...")
    
    out_dir = Path("artifacts") / ISO3
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load Data
    score_path = out_dir / f"schools/{ISO3}_school_vulnerability.csv"
    if not score_path.exists():
        print(f"✗ Base school score data missing: {score_path}")
        return
    
    schools_df = pd.read_csv(score_path)

    hybrid_path = out_dir / f"{ISO3}_hybrid_vulnerability_index.csv"
    if not hybrid_path.exists():
        print(f"✗ Hybrid index missing: {hybrid_path}")
        return
    
    hybrid_df = pd.read_csv(hybrid_path)

    # 2. Alignment & Mapping
    mapping_path = out_dir / "admin_mapping.json"
    official_to_acled = {}
    if mapping_path.exists():
        with open(mapping_path, 'r', encoding='utf-8') as f:
            mapping_data = json.load(f)
            official_to_acled = mapping_data.get("official_to_acled", {})

    if "province" not in schools_df.columns:
        if "Admin2_join" in schools_df.columns:
            schools_df["province"] = schools_df["Admin2_join"]
        elif "Admin2" in schools_df.columns:
            schools_df["province"] = schools_df["Admin2"]
        else:
            schools_df["province"] = "Unknown"

    schools_df["Admin2_ACLED"] = schools_df["province"].map(official_to_acled).fillna(schools_df["province"])

    # 3. Dynamic Calculation
    aggregated = {}
    available_years = sorted(hybrid_df["year"].unique().tolist())
    
    # Initialize list for tracking yearly scores per school
    school_yearly_scores = []

    print(f"  → Processing {len(available_years)} years for {len(schools_df)} schools...")

    # Pivot hybrid_df for faster lookup
    # index: Admin2, columns: year, values: score
    score_lookup = hybrid_df.pivot(index="Admin2", columns="year", values="score").to_dict()

    for idx, s in schools_df.iterrows():
        prov = s["Admin2_ACLED"]
        y_scores = {}
        at_risk_years = []
        
        for year in available_years:
            y_str = str(year)
            v_score = score_lookup.get(year, {}).get(prov, 0)
            y_scores[y_str] = float(v_score)

            # Threshold: High risk (score > 0.5)
            if v_score > 0.5:
                at_risk_years.append(year)
                
                if y_str not in aggregated:
                    aggregated[y_str] = {}
                if prov not in aggregated[y_str]:
                    aggregated[y_str][prov] = {"count": 0, "schools": []}
                
                aggregated[y_str][prov]["count"] += 1
                name = str(s.get("name", "Unknown"))
                if name.lower() in ["nan", "unnamed school", "none"]:
                    name = "Unknown"

                aggregated[y_str][prov]["schools"].append({
                    "name": name,
                    "province": str(prov),
                    "lat": float(s.get("latitude", 0)),
                    "lon": float(s.get("longitude", 0)),
                    "v_score": float(v_score)
                })
        
        school_yearly_scores.append({
            "yearly_scores": y_scores,
            "at_risk_years": at_risk_years
        })

    # Add the results back to schools_df
    scores_df = pd.DataFrame(school_yearly_scores)
    schools_df["yearly_scores"] = scores_df["yearly_scores"]
    schools_df["at_risk_years"] = scores_df["at_risk_years"]

    # 4. Final Formatting
    schools_df["v_score"] = schools_df["yearly_scores"].apply(lambda x: x.get("2024", x.get(str(available_years[-1]), 0)))
    schools_df["trauma"] = (schools_df["v_score"] * 10).astype(int)
    
    # Save Outputs
    out_path = out_dir / "province_at_risk_stats.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(aggregated, f, separators=(",", ":"), ensure_ascii=False)
    
    scores_json_path = out_dir / "school_vulnerability_scores.json"
    # DO NOT drop yearly_scores, export_map_data needs it!
    final_json_df = schools_df.drop(columns=["Admin2_ACLED"])
    final_json_df.to_json(scores_json_path, orient="records")

    print(f"✅ Success! Dynamic stats saved to {out_path}")
    print(f"✅ School scores (with yearly_scores) saved to {scores_json_path}")

if __name__ == "__main__":
    aggregate_at_risk_schools()
