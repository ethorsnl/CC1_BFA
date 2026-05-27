"""
02_fetch_education.py
=====================
Fetches education data from three sources in priority order:

  1. DHS API (api.dhsprogram.com)
     → Subnational (Admin1 region) education indicators from household surveys
     → Best for: school attendance by age/sex, literacy, years of schooling
     → Coverage: BFA surveys 1993, 1998, 2003, 2010, 2021
     → No API key required for aggregated indicators

  2. World Bank API (api.worldbank.org)
     → National-level time series 1990–present
     → Best for: enrolment rates, completion rates, OOS trends over time
     → No API key required

  3. HDX direct CSVs (data.humdata.org)
     → Pre-packaged World Bank + DHS data for BFA, direct download
     → Fallback if APIs are rate-limited or restructured

WHY NOT OPRI?
  UNESCO OPRI only contains whatever national education ministries
  report to UNESCO. For conflict-affected countries like BFA, this
  is sparse (2–3 indicators, national level only, significant lag).
  DHS household surveys are more reliable for subnational analysis.

Output:
  data/raw/education/dhs_subnational_{ISO3}.csv     subnational DHS
  data/raw/education/worldbank_national_{ISO3}.csv  WB time series
  data/raw/education/hdx_education_{ISO3}.csv       HDX direct CSV
"""

import os, io, time, requests, pandas as pd, argparse
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

# DHS education indicator IDs (aggregated — no key needed)
DHS_EDUCATION_INDICATORS = [
    "ED_EDUC_W_MYS",   # Mean years of schooling, women 15-49
    "ED_EDUC_M_MYS",   # Mean years of schooling, men 15-24
    "ED_LITR_W_LIT",   # Literacy rate, women 15-24
    "ED_LITR_M_LIT",   # Literacy rate, men 15-24
    "ED_ATND_B_ATT",   # School attendance, boys 6-17
    "ED_ATND_G_ATT",   # School attendance, girls 6-17
    "ED_ATND_B_A06",   # School attendance, boys primary age
    "ED_ATND_G_A06",   # School attendance, girls primary age
    "ED_ATND_B_A12",   # School attendance, boys secondary age
    "ED_ATND_G_A12",   # School attendance, girls secondary age
    "ED_EDUC_W_NED",   # Women with no education
    "ED_EDUC_M_NED",   # Men with no education
]

# World Bank education indicator codes
WB_INDICATORS = {
    "SE.PRM.NENR":    "Net enrolment rate, primary",
    "SE.PRM.NENR.FE": "Net enrolment rate, primary, female",
    "SE.PRM.NENR.MA": "Net enrolment rate, primary, male",
    "SE.PRM.CMPT.ZS": "Primary completion rate",
    "SE.PRM.UNER":    "Children out of school, primary (number)",
    "SE.PRM.UNER.FE": "Children out of school, primary, female",
    "SE.PRM.UNER.MA": "Children out of school, primary, male",
    "SE.ADT.LITR.ZS": "Adult literacy rate, 15+",
    "SE.SEC.NENR":    "Net enrolment rate, secondary",
    "SE.PRM.TENR":    "Adjusted net enrolment rate, primary",
}

# HDX confirmed resource URLs (hardcoded for BFA as baseline, others would need search)
HDX_STATIC_RESOURCES = {
    "BFA": {
        "worldbank_education": "https://data.humdata.org/dataset/b43b9310-b80c-4e8a-9143-f5616fd36fbc/resource/28eec0ac-a4dd-411a-b799-b536459e628f/download/education_bfa.csv",
        "dhs_education":       "https://data.humdata.org/dataset/dhs-subnational-data-for-burkina-faso/resource/ad9f58c1-1387-4265-98ea-e60fed1e3a0b/download/",
    }
}

OUT_DIR = Path("data/raw/education")
OUT_DIR.mkdir(parents=True, exist_ok=True)
HEADERS = {"User-Agent": "education-risk-research/1.0"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_dhs_country_code(iso3: str) -> str:
    """Dynamically look up DHS country code from ISO3."""
    url = "https://api.dhsprogram.com/rest/dhs/countries?f=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        for country in data.get("Data", []):
            if country.get("ISO3_CountryCode") == iso3:
                return country.get("DHS_CountryCode")
    except Exception as e:
        print(f"    Warning: Could not fetch DHS country map: {e}")
    
    return None

# ── Source 1: DHS API ─────────────────────────────────────────────────────────

def fetch_dhs_subnational(iso3: str) -> pd.DataFrame:
    """
    Fetch subnational education indicators from the DHS API.
    Returns one row per (survey, region, indicator).
    """
    dhs_code = get_dhs_country_code(iso3)
    if not dhs_code:
        print(f"  ⚠ No DHS code found for {iso3}")
        return pd.DataFrame()

    ind_str = ",".join(DHS_EDUCATION_INDICATORS)
    url = (
        f"https://api.dhsprogram.com/rest/dhs/data"
        f"?countryIds={dhs_code}"
        f"&indicatorIds={ind_str}"
        f"&breakdown=subnational"
        f"&f=json"
        f"&perpage=5000"
    )

    print(f"  DHS API: {dhs_code} ({iso3}) subnational education...")
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 403:
            print("    403 — DHS API blocked from this network")
            return pd.DataFrame()
        r.raise_for_status()
        data = r.json()
        records = data.get("Data", [])
        if not records:
            print(f"    No data returned for {iso3} — checking indicators...")
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df["iso3"] = iso3
        df["source"] = "DHS"
        print(f"    ✓ {len(df):,} rows | surveys: {df['SurveyYear'].unique().tolist()}")
        return df

    except Exception as e:
        print(f"    Error: {e}")
        return pd.DataFrame()


# ── Source 2: World Bank API ──────────────────────────────────────────────────

def fetch_worldbank(iso3: str) -> pd.DataFrame:
    """
    Fetch national-level education time series from World Bank API.
    Returns long-format DataFrame: one row per (indicator, year).
    """
    all_rows = []

    for code, name in WB_INDICATORS.items():
        # WB API supports ISO3 directly
        url = (
            f"https://api.worldbank.org/v2/country/{iso3}"
            f"/indicator/{code}?format=json&per_page=50&mrv=25"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            data = r.json()
            if len(data) < 2 or not data[1]:
                continue
            for obs in data[1]:
                if obs.get("value") is not None:
                    all_rows.append({
                        "iso3":           iso3,
                        "indicator_code": code,
                        "indicator_name": name,
                        "year":           int(obs["date"]),
                        "value":          float(obs["value"]),
                        "source":         "WorldBank",
                    })
            time.sleep(0.1)
        except Exception as e:
            print(f"    Error fetching {code}: {e}")

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows).sort_values(["indicator_code", "year"])
    print(f"    ✓ {len(df):,} rows | indicators: {df['indicator_code'].nunique()}")
    return df


# ── Source 3: HDX direct CSV ──────────────────────────────────────────────────

def fetch_hdx_csv(iso3: str) -> dict[str, pd.DataFrame]:
    """
    Download pre-packaged education CSVs from HDX.
    """
    results = {}
    
    resources = HDX_STATIC_RESOURCES.get(iso3)
    if not resources:
        print(f"    HDX direct URLs not configured for {iso3} — skipping")
        return results

    for name, url in resources.items():
        try:
            r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
            if r.status_code == 200:
                df = pd.read_csv(io.BytesIO(r.content), low_memory=False)
                df["source"] = "HDX"
                df["iso3"]   = iso3
                results[name] = df
                print(f"    ✓ {name}: {len(df):,} rows")
            else:
                print(f"    HTTP {r.status_code} for {name}")
        except Exception as e:
            print(f"    Error fetching {name}: {e}")

    return results


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch education data for a country.")
    parser.add_argument("iso3", help="ISO3 country code (e.g., BFA, MLI, NER)")
    args = parser.parse_args()

    iso3 = args.iso3.upper()
    print(f"Fetching education data for {iso3}...\n")

    # 1. DHS subnational
    print("[1/3] DHS API — subnational education indicators")
    df_dhs = fetch_dhs_subnational(iso3)
    if not df_dhs.empty:
        path = OUT_DIR / f"dhs_subnational_{iso3}.csv"
        df_dhs.to_csv(path, index=False)
        print(f"  Saved → {path}\n")
    else:
        print("  No DHS data found.\n")

    # 2. World Bank national time series
    print("[2/3] World Bank API — national time series")
    df_wb = fetch_worldbank(iso3)
    if not df_wb.empty:
        path = OUT_DIR / f"worldbank_national_{iso3}.csv"
        df_wb.to_csv(path, index=False)
        print(f"  Saved → {path}\n")

    # 3. HDX direct CSV fallback
    print("[3/3] HDX direct CSV — fallback")
    hdx_data = fetch_hdx_csv(iso3)
    for name, df in hdx_data.items():
        path = OUT_DIR / f"hdx_{name}_{iso3}.csv"
        df.to_csv(path, index=False)
        print(f"  Saved {name} → {path}")

    print(f"\n✅  Education data fetch complete for {iso3}")