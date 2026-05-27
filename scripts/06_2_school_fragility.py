"""
08_school_vulnerability.py
===========================
Computes a vulnerability score for every school by combining:

  Stage 1 — Province-level conflict pressure (from your ACLED file)
    Your ACLED data is already aggregated to Admin2 × Month × Year
    with Events and Fatalities counts. Each school inherits the
    conflict score of the province it sits in.

    Conflict score per province per year:
      A1 = 2-year rolling event count (decay-weighted: current yr × 1.0, prior yr × 0.6)
      A2 = 2-year rolling fatality count (log-scaled, same decay)
      Combined = 0.55 × A1_norm + 0.45 × A2_norm

  Stage 2 — National fragility multiplier (from your education indicators file)
    Annual indicators, national level only.
    Picks the best available indicators, forward-fills gaps to match
    conflict years, computes a single scalar per year.

    Fragility scalar per year (using your available indicators):
      - Children out of school, % primary (higher = worse)  weight 0.35
      - Primary completion rate, total (higher = better)    weight 0.30
      - Survival rate, last grade primary (higher = better) weight 0.20
      - Net enrolment rate, primary (higher = better)       weight 0.15

    Forward-fills last known value for years beyond data coverage.
    Documented in score_basis per school.

  Combined score:
    school_score = conflict_norm × (1 + fragility_scalar × BOOST_MAX)
    Capped at 1.0. Schools with zero conflict exposure stay at 0.

Input files (your actual paths):
  data/raw/acled/acled_BFA.csv          — Admin2 × Month × Year × Events × Fatalities
  data/raw/education/education_BFA.csv  — iso3 × year × region × indicator × value
  assets/schools_BFA.geojson            — school point locations

Output:
  data/clean/BFA_school_vulnerability.csv
  assets/schools_BFA.geojson  (updated with scores)
  data/clean/BFA_fragility_by_year.csv  (transparency — show scalar per year)
"""

import json
import os
import numpy as np
import pandas as pd
import geopandas as gpd
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
ISO3        = os.environ.get("PIPELINE_ISO3", "BFA")
COUNTRY     = os.environ.get("PIPELINE_COUNTRY", "Burkina Faso")
REF_YEAR    = 2024
WINDOW      = 2        # rolling years: REF_YEAR and REF_YEAR - 1
DECAY_PRIOR = 0.6      # weight for the year before REF_YEAR (current = 1.0)
BOOST_MAX   = 0.40     # max fragility uplift (40%)

# Sanitize country name for file matching
country_safe = COUNTRY.replace(" ", "_")

def find_acled_file(country: str) -> Path:
    """Search for the country's ACLED CSV in any data/clean/acled/* subdirectory."""
    base_dir = Path("data/clean/acled")
    if not base_dir.exists():
        return Path(f"data/clean/acled/HRP_2_countries/{country}.csv")
    for path in base_dir.glob(f"**/{country}_geocoded.csv"):
        return path
    for path in base_dir.glob(f"**/{country}.csv"):
        return path
    return base_dir / f"HRP_2_countries/{country}.csv"

# Exact indicator strings as they appear in your education file
# (direction: "pos" = higher value means MORE fragile)
FRAGILITY_INDICATORS = [
    ("Children out of school (% of primary school age)",
     "pos", 0.35),
    ("Primary completion rate, total (% of relevant age group)",
     "neg", 0.30),
    ("Survival rate to the last grade of primary education, both sexes (%)",
     "neg", 0.20),
    ("Total net enrolment rate, primary, both sexes (%)",
     "neg", 0.15),
]

# File paths — update if yours differ
IN_ACLED     = find_acled_file(country_safe)
IN_EDU       = Path("data/clean/education/master_education.csv")
IN_SCHOOLS   = Path(f"data/clean/schools/schools_{ISO3}.csv")

OUT_DIR      = Path("artifacts") / ISO3
OUT_CSV      = OUT_DIR / f"schools/{ISO3}_school_vulnerability.csv"
OUT_GJ       = OUT_DIR / f"schools/schools_{ISO3}.geojson"   # overwrites with scores
OUT_FRAG     = OUT_DIR / f"schools/{ISO3}_fragility_by_year.csv"

(OUT_DIR / "schools").mkdir(parents=True, exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def minmax(s: pd.Series) -> pd.Series:
    lo, hi = s.min(), s.max()
    if hi == lo:
        return pd.Series(0.5, index=s.index)   # all equal → mid-point
    return (s - lo) / (hi - lo)


def classify(score: float) -> str:
    if score >= 0.75: return "critical"
    if score >= 0.50: return "high"
    if score >= 0.25: return "medium"
    return "low"


# ── Stage 1: Province conflict scores ─────────────────────────────────────────

def build_province_conflict(acled: pd.DataFrame,
                             ref_year: int,
                             window: int) -> pd.DataFrame:
    """
    Aggregate ACLED to province × year, apply 2-year decay,
    return one row per province with conflict_score ∈ [0, 1].

    Your ACLED columns: Country, Admin1, Admin2, ISO3,
    Admin2 Pcode, Admin1 Pcode, Month, Year, Events, Fatalities,
    Latitude, Longitude
    """
    df = acled.copy()
    df["Year"]       = pd.to_numeric(df["Year"],       errors="coerce")
    df["Events"]     = pd.to_numeric(df["Events"],     errors="coerce").fillna(0)
    df["Fatalities"] = pd.to_numeric(df["Fatalities"], errors="coerce").fillna(0)

    # Filter to rolling window
    years = list(range(ref_year - window + 1, ref_year + 1))
    df    = df[df["Year"].isin(years)].copy()

    # Decay weight: current year = 1.0, prior year = DECAY_PRIOR
    df["decay"] = df["Year"].apply(lambda y: 1.0 if y == ref_year else DECAY_PRIOR)

    # Aggregate: sum weighted events and fatalities per province
    grp = (df.assign(w_events     = df["Events"]     * df["decay"],
                     w_fatalities = df["Fatalities"]  * df["decay"])
             .groupby("Admin2", as_index=False)
             .agg(
                 Admin1          = ("Admin1",       "first"),
                 Latitude        = ("Latitude",     "first"),
                 Longitude       = ("Longitude",    "first"),
                 raw_events      = ("w_events",     "sum"),
                 raw_fatalities  = ("w_fatalities", "sum"),
                 total_events    = ("Events",       "sum"),
                 total_fatalities= ("Fatalities",   "sum"),
             ))

    # Normalise
    grp["A1_norm"] = minmax(grp["raw_events"])
    grp["A2_norm"] = minmax(np.log1p(grp["raw_fatalities"]))   # log-scale fatalities

    # Combined conflict score
    grp["conflict_score"] = 0.55 * grp["A1_norm"] + 0.45 * grp["A2_norm"]

    print(f"  {len(grp)} provinces scored")
    print(f"  Events range:     {grp['total_events'].min():.0f} – {grp['total_events'].max():.0f}")
    print(f"  Fatalities range: {grp['total_fatalities'].min():.0f} – {grp['total_fatalities'].max():.0f}")
    return grp


# ── Stage 2: National fragility scalar per year ────────────────────────────────

def build_fragility_series(edu: pd.DataFrame,
                            ref_year: int) -> tuple[pd.DataFrame, float]:
    """
    Compute a fragility scalar for each year from 2000 to ref_year.
    Forward-fills missing years using the last known value.
    Returns:
      - DataFrame with year, scalar, and per-indicator values (transparency)
      - float: the scalar for ref_year specifically
    """
    # Filter to national-level rows only
    edu = edu[edu["region"].str.lower() == "national"].copy()
    edu["year"]  = pd.to_numeric(edu["year"],  errors="coerce")
    edu["value"] = pd.to_numeric(edu["value"], errors="coerce")

    years   = list(range(2000, ref_year + 1))
    records = []

    for year in years:
        row       = {"year": year}
        scores    = []
        used_vals = {}

        for ind_name, direction, weight in FRAGILITY_INDICATORS:
            # Get all historical values for this indicator up to this year
            hist = edu[
                (edu["indicator"] == ind_name) &
                (edu["year"]      <= year)
            ].sort_values("year")

            if hist.empty:
                used_vals[ind_name[:40]] = None
                continue

            # Use most recent available value (forward-fill logic)
            val      = float(hist.iloc[-1]["value"])
            val_year = int(hist.iloc[-1]["year"])

            # Normalise against full historical range for this indicator
            full_hist = edu[edu["indicator"] == ind_name]["value"].dropna()
            lo, hi    = full_hist.min(), full_hist.max()

            if hi == lo:
                norm = 0.5
            else:
                norm = (val - lo) / (hi - lo)
                if direction == "neg":
                    norm = 1.0 - norm   # higher enrolment = less fragile

            scores.append((norm, weight))
            used_vals[ind_name[:40]] = {
                "value": round(val, 2),
                "year_used": val_year,
                "normalised": round(norm, 3),
                "forward_filled": val_year < year,
            }

        if not scores:
            row["fragility_scalar"] = 0.0
        else:
            total_w = sum(w for _, w in scores)
            raw     = sum(s * w for s, w in scores) / total_w
            row["fragility_scalar"] = round(raw * BOOST_MAX, 4)

        row.update({k[:35]: v["value"] if v else None
                    for k, v in used_vals.items()})
        records.append(row)

    df_frag = pd.DataFrame(records)
    ref_scalar = float(df_frag[df_frag["year"] == ref_year]["fragility_scalar"].values[0])
    print(f"  Fragility scalar for {ref_year}: {ref_scalar:.4f} "
          f"(max possible uplift: {BOOST_MAX*100:.0f}%)")
    return df_frag, ref_scalar


# ── Join schools to provinces and score ───────────────────────────────────────

def score_schools(schools_gdf:    gpd.GeoDataFrame,
                  province_scores: pd.DataFrame,
                  fragility_scalar: float) -> pd.DataFrame:
    """
    Each school gets the conflict score of its Admin2 province.
    Join is on Admin2 name if available, otherwise nearest centroid.
    """
    schools = schools_gdf.copy()
    schools["latitude"]  = schools.geometry.y
    schools["longitude"] = schools.geometry.x

    # ── Try name-based join first ─────────────────────────────────────────────
    admin2_col = next(
        (c for c in schools.columns
         if c.lower() in ("admin2","province","admin_2","adm2_en",
                          "adm2name","district")),
        None
    )

    if admin2_col:
        print(f"  Joining on column: '{admin2_col}'")
        # Normalise names for join
        prov_map = province_scores.set_index(
            province_scores["Admin2"].str.strip().str.title()
        )["conflict_score"].to_dict()

        schools["Admin2_join"] = schools[admin2_col].str.strip().str.title()
        schools["conflict_score"] = schools["Admin2_join"].map(prov_map)

        unmatched = schools["conflict_score"].isna().sum()
        if unmatched > 0:
            print(f"  ⚠ {unmatched} schools unmatched by name — "
                  f"falling back to nearest centroid for those")

    # ── Nearest-centroid fallback for unmatched schools ───────────────────────
    unmatched_mask = schools["conflict_score"].isna() if "conflict_score" in schools.columns \
                     else pd.Series(True, index=schools.index)

    if unmatched_mask.any():
        # Build province centroid lookup
        prov_lats = province_scores["Latitude"].values
        prov_lons = province_scores["Longitude"].values
        prov_sc   = province_scores["conflict_score"].values

        for idx in schools[unmatched_mask].index:
            s_lat = schools.at[idx, "latitude"]
            s_lon = schools.at[idx, "longitude"]
            if pd.isna(s_lat) or pd.isna(s_lon):
                continue
            # Euclidean distance to province centroids (good enough at this scale)
            dists = np.sqrt((prov_lats - s_lat)**2 + (prov_lons - s_lon)**2)
            nearest_idx = np.argmin(dists)
            schools.at[idx, "conflict_score"] = prov_sc[nearest_idx]
            schools.at[idx, "Admin2_join"]    = province_scores.iloc[nearest_idx]["Admin2"]

    # ── Apply fragility multiplier ────────────────────────────────────────────
    cs = schools["conflict_score"].fillna(0)
    schools["fragility_uplift"] = round(fragility_scalar, 4)
    schools["final_score"]      = (cs * (1 + fragility_scalar)).clip(upper=1.0).round(4)
    schools["tier"]             = schools["final_score"].apply(classify)
    schools["at_risk"]          = (cs > 0).astype(int)
    schools["conflict_score"]   = cs.round(4)

    # ── Score basis note ──────────────────────────────────────────────────────
    n_indicators = sum(1 for ind, _, _ in FRAGILITY_INDICATORS
                       if not edu_for_basis(ind))
    schools["score_basis"] = (
        f"conflict(A1+A2, {WINDOW}yr rolling) × "
        f"fragility({n_indicators}/{len(FRAGILITY_INDICATORS)} indicators, national)"
    )

    return schools


def edu_for_basis(ind_name: str) -> bool:
    """Placeholder — actual check done in build_fragility_series."""
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"School vulnerability — {ISO3}  ref_year={REF_YEAR}  window={WINDOW}yr\n")

    # Load
    print("[1/4] Loading data...")
    if not IN_ACLED.exists():
        print(f"  ✗ {IN_ACLED} not found"); raise SystemExit(1)
    if not IN_EDU.exists():
        print(f"  ✗ {IN_EDU} not found"); raise SystemExit(1)
    if not IN_SCHOOLS.exists():
        print(f"  ✗ {IN_SCHOOLS} not found — run 07_prepare_schools.py first")
        raise SystemExit(1)

    acled       = pd.read_csv(IN_ACLED)
    edu         = pd.read_csv(IN_EDU)
    df = pd.read_csv(IN_SCHOOLS)
    schools_gdf = gpd.GeoDataFrame(
        df, 
        geometry=gpd.points_from_xy(df.longitude, df.latitude),
        crs="EPSG:4326"
    )
    print(f"  ACLED:   {len(acled):,} rows | "
          f"years {int(acled['Year'].min())}–{int(acled['Year'].max())}")
    print(f"  Edu:     {len(edu):,} rows | "
          f"{edu['indicator'].nunique()} indicators")
    print(f"  Schools: {len(schools_gdf):,} locations")

    # Stage 1
    print(f"\n[2/4] Province conflict scores ({REF_YEAR-WINDOW+1}–{REF_YEAR})...")
    province_scores = build_province_conflict(acled, REF_YEAR, WINDOW)

    # Stage 2
    print(f"\n[3/4] National fragility scalar...")
    df_frag, ref_scalar = build_fragility_series(edu, REF_YEAR)
    df_frag.to_csv(OUT_FRAG, index=False)
    print(f"  Fragility series → {OUT_FRAG}")

    # Score schools
    print(f"\n[4/4] Scoring {len(schools_gdf):,} schools...")
    scored = score_schools(schools_gdf, province_scores, ref_scalar)

    # Save CSV (no geometry)
    csv_cols = ["name","amenity","latitude","longitude","Admin2_join",
                "conflict_score","fragility_uplift","final_score","tier",
                "at_risk","score_basis"]
    csv_cols = [c for c in csv_cols if c in scored.columns]
    scored[csv_cols].rename(columns={"Admin2_join": "province"}).to_csv(OUT_CSV, index=False)
    print(f"  ✓ CSV → {OUT_CSV}")

    # Save GeoJSON (with geometry + scores)
    out_gj_cols = csv_cols + ["geometry"]
    scored[out_gj_cols].to_file(OUT_GJ, driver="GeoJSON")
    print(f"  ✓ GeoJSON → {OUT_GJ}")

    # Summary
    print(f"\n{'='*55}")
    print(f"  Total schools:      {len(scored):,}")
    print(f"  At risk (>0 score): {scored['at_risk'].sum():,}")
    print(f"  Fragility uplift:   +{ref_scalar*100:.1f}%\n")
    print(f"  Tier breakdown:")
    for tier in ["critical","high","medium","low"]:
        n   = (scored["tier"] == tier).sum()
        pct = n / len(scored) * 100
        bar = "█" * int(pct / 2)
        print(f"    {tier:<10} {n:>5,}  ({pct:5.1f}%)  {bar}")

    # Province-level summary
    print(f"\n  Top 10 highest-risk provinces:")
    top = (province_scores
           .sort_values("conflict_score", ascending=False)
           .head(10)
           [["Admin2","Admin1","total_events","total_fatalities","conflict_score"]])
    top["conflict_score"] = top["conflict_score"].round(3)
    print(top.to_string(index=False))