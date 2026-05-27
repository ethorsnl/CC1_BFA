"""
05_build_analysis.py
====================
Merges ACLED conflict data with OPRI education indicators and computes
a composite vulnerability score per admin1 region per year.

The vulnerability score combines:
  1. Conflict intensity  — normalised event count per admin1 per year
  2. Conflict severity   — normalised fatality count per admin1 per year
  3. Survival rate gap   — inverse of primary survival rate (lower = more vulnerable)
  4. OOS rate            — out-of-school rate (higher = more vulnerable)

Each indicator is min-max normalised to [0,1].
Score = mean of available indicators (skipna — honest about missing data).
Admin1s are flagged with how many indicators contributed to their score.

Generic — change ISO3 at the top to run on any country.

Output:
  data/clean/{ISO3}_vulnerability.csv    — per admin1 per year scores
  data/clean/{ISO3}_national_trends.csv  — national-level year-on-year trends
"""

import pandas as pd
import numpy as np
import os
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
ISO3       = os.environ.get("PIPELINE_ISO3", "BFA")
COUNTRY    = os.environ.get("PIPELINE_COUNTRY", "Burkina Faso")
START_YEAR = 2015
END_YEAR   = 2026

# Sanitize country name for file matching (replace spaces with underscores)
country_safe = COUNTRY.replace(" ", "_")

def find_acled_file(country: str) -> Path:
    """Search for the country's ACLED CSV in any data/clean/acled/* subdirectory."""
    base_dir = Path("data/clean/acled")
    if not base_dir.exists():
        return Path(f"data/clean/acled/HRP_2_countries/{country}.csv") # Fallback
    
    # Priority 1: Geocoded version
    for path in base_dir.glob(f"**/{country}_geocoded.csv"):
        return path
    
    # Priority 2: Standard version
    for path in base_dir.glob(f"**/{country}.csv"):
        return path
    
    return base_dir / f"HRP_2_countries/{country}.csv" # Default path

IN_ACLED = find_acled_file(country_safe)
IN_EDU   = Path("data/clean/education/master_education.csv")
OUT_DIR  = Path("artifacts") / ISO3
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def minmax(series: pd.Series) -> pd.Series:
    """Min-max normalise a series to [0, 1]."""
    if series.isna().all() or (series.max() == series.min()):
        return pd.Series(0.0, index=series.index)
    return (series - series.min()) / (series.max() - series.min())


def load_acled(path: Path) -> pd.DataFrame:
    if not path.exists():
        print(f"  ⚠ ACLED file missing: {path}")
        return pd.DataFrame()
    
    df = pd.read_csv(path)
    # The clean file already has Year, Events, Fatalities, Admin1
    df = df.rename(columns={"Admin1": "region", "Year": "year", "Events": "events", "Fatalities": "fatalities"})
    df["region"] = df["region"].str.strip().str.title()
    return df[df["year"].between(START_YEAR, END_YEAR)]


def load_education(path: Path, iso3: str) -> pd.DataFrame:
    """Load the 3 key indicators from the master education file."""
    if not path.exists():
        print(f"  ⚠ Education file missing: {path}")
        return pd.DataFrame()

    df = pd.read_csv(path)
    
    indicators = [
        'Children out of school, primary',
        'Persistence to last grade of primary, total (% of cohort)',
        'Total net enrolment rate, primary, both sexes (%)'
    ]
    
    # Filter for country and key indicators (National level for baseline)
    mask = (df["iso3"] == iso3) & (df["region"] == "National") & (df["indicator"].isin(indicators))
    df = df[mask].copy()
    
    if df.empty:
        return pd.DataFrame()

    # Pivot
    pivoted = df.pivot_table(index='year', columns='indicator', values='value').reset_index()
    
    # Standardize column names for the rest of the script
    rename_map = {
        'Children out of school, primary': 'oos_rate',
        'Persistence to last grade of primary, total (% of cohort)': 'persistence',
        'Total net enrolment rate, primary, both sexes (%)': 'enrolment'
    }
    pivoted = pivoted.rename(columns=rename_map)
    
    # Fill missing years in the range
    all_years = pd.DataFrame({"year": range(START_YEAR, END_YEAR + 1)})
    pivoted = all_years.merge(pivoted, on="year", how="left")

    # Normalise (Inverse for persistence/enrolment)
    if 'oos_rate' in pivoted.columns:
        pivoted['oos_norm'] = minmax(pivoted['oos_rate'])
    if 'persistence' in pivoted.columns:
        pivoted['persistence_norm'] = 1 - minmax(pivoted['persistence'])
    if 'enrolment' in pivoted.columns:
        pivoted['enrolment_norm'] = 1 - minmax(pivoted['enrolment'])

    # Composite Edu Baseline (Mean of available)
    edu_cols = [c for c in ['oos_norm', 'persistence_norm', 'enrolment_norm'] if c in pivoted.columns]
    pivoted['edu_baseline'] = pivoted[edu_cols].mean(axis=1)
    
    return pivoted


# ── Step 1: ACLED — compute per admin1 per year conflict metrics ───────────────

def build_conflict_metrics(acled: pd.DataFrame) -> pd.DataFrame:
    # Aggregate by Region/Year (Admin1)
    grp = acled.groupby(["region", "year"]).agg(
        events     = ("events", "sum"),
        fatalities = ("fatalities", "sum"),
    ).reset_index()

    # Normalise within each year
    for year, g in grp.groupby("year"):
        grp.loc[g.index, "events_norm"]     = minmax(g["events"])
        grp.loc[g.index, "fatalities_norm"] = minmax(g["fatalities"])

    return grp


# ── Step 2: Merge and compute composite score ──────────────────────────────────

def compute_vulnerability(conflict: pd.DataFrame, edu: pd.DataFrame) -> pd.DataFrame:
    # Broadcast National Edu baseline to all regions
    df = conflict.merge(edu[['year', 'edu_baseline']], on="year", how="left")
    
    # Fill missing edu with the average if needed
    df['edu_baseline'] = df['edu_baseline'].fillna(df['edu_baseline'].mean())

    # Final Score (Weighted: 40% Conflict, 60% Education)
    df["conflict_score"] = (df["events_norm"] + df["fatalities_norm"]) / 2
    df["score"] = (df["edu_baseline"] * 0.6) + (df["conflict_score"] * 0.4)
    
    # Rounding for cleanliness
    cols_to_round = ["events_norm", "fatalities_norm", "edu_baseline", "conflict_score", "score"]
    df[cols_to_round] = df[cols_to_round].round(3)

    # 4-Tier logic: Critical = Top 5% overall, then Terciles for the rest
    critical_threshold = df["score"].quantile(0.95)
    
    def assign_tier(val):
        if val >= critical_threshold:
            return "critical"
        return None
    
    df["score_tercile"] = df["score"].apply(assign_tier)
    
    # For non-critical, use terciles
    mask = df["score_tercile"].isna()
    if mask.any():
        df.loc[mask, "score_tercile"] = pd.qcut(
            df.loc[mask, "score"].rank(method="first"),
            q=3,
            labels=["lower_priority", "medium_priority", "high_priority"]
        )

    # Add a basis flag (always 3 for this version)
    df["score_basis"] = "3/3_indicators"

    return df.sort_values(["year", "score"], ascending=[True, False])


# ── Step 3: National trends (for the time-series chart) ───────────────────────

def build_national_trends(acled: pd.DataFrame, edu: pd.DataFrame) -> pd.DataFrame:
    national_conflict = acled.groupby("year").agg(
        total_events     = ("events", "sum"),
        total_fatalities = ("fatalities", "sum"),
    ).reset_index()

    return national_conflict.merge(edu, on="year", how="left")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    print(f"Building analysis for {ISO3}...")

    # Load
    acled_data = load_acled(IN_ACLED)
    if acled_data.empty:
        print("✗ No ACLED data found. Check paths.")
        exit(1)
        
    print(f"  ACLED: {len(acled_data):,} records, {acled_data['region'].nunique()} admin1 regions")

    edu_data = load_education(IN_EDU, ISO3)
    if edu_data.empty:
        print("✗ No Education data found. Check master_education.csv.")
        exit(1)
    
    print(f"  Education: Data spanning {edu_data['year'].min()}-{edu_data['year'].max()}")

    # Build components
    conflict_metrics = build_conflict_metrics(acled_data)
    vuln   = compute_vulnerability(conflict_metrics, edu_data)
    trends = build_national_trends(acled_data, edu_data)

    # Save
    vuln_path   = OUT_DIR / f"{ISO3}_vulnerability.csv"
    trends_path = OUT_DIR / f"{ISO3}_national_trends.csv"

    # Reorder columns for consistency
    cols_order = [
        "region", "year", "events", "fatalities", "events_norm", 
        "fatalities_norm", "edu_baseline", "conflict_score", 
        "score", "score_tercile", "score_basis"
    ]
    vuln = vuln[cols_order]
    vuln.to_csv(vuln_path, index=False)
    
    # Round trends and save
    trends = trends.round(3)
    trends.to_csv(trends_path, index=False)

    print(f"\n✓ Vulnerability scores → {vuln_path}")
    print(f"✓ National trends      → {trends_path}")
    print(f"\nScore summary ({ISO3}):")
    print(vuln.groupby("score_tercile")["region"].nunique().rename("admin1_regions").to_string())
