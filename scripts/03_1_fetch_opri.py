"""
03_1_fetch_opri.py
==================
Streams the UNESCO UIS OPRI bulk CSV and filters to target countries
and education indicators. Uses numeric IDs mapped from OPRI_LABEL.csv.

Output: data/raw/opri/opri_{ISO3}.csv per country
"""

import io, zipfile, requests, pandas as pd
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
START_YEAR       = 2000
END_YEAR         = 2026

# Search terms to dynamically find indicators from the label file
SEARCH_TERMS = [
    "Net enrolment",
    "Out-of-school",
    "Survival rate",
    "Gross enrolment",
    "Pupil-teacher"
]

OPRI_ZIP_URL = "https://download.uis.unesco.org/bdds/202509/OPRI.zip"
TARGET_CSV = "OPRI_DATA_NATIONAL.csv"
LABEL_CSV = "OPRI_LABEL.csv"

OUT_DIR = Path("data/raw/education/opri")
OUT_DIR.mkdir(parents=True, exist_ok=True)
HEADERS = {"User-Agent": "education-risk-research/1.0"}

def fetch_and_filter() -> pd.DataFrame:
    print(f"Streaming OPRI bulk data...")
    r = requests.get(OPRI_ZIP_URL, stream=True, headers=HEADERS, timeout=300)
    r.raise_for_status()

    buf = io.BytesIO()
    for chunk in r.iter_content(1 << 20):
        buf.write(chunk)
    buf.seek(0)

    with zipfile.ZipFile(buf) as zf:
        # Load labels
        with zf.open(LABEL_CSV) as f:
            labels = pd.read_csv(f)
            # Find relevant indicators
            mask = labels["INDICATOR_LABEL_EN"].str.contains('|'.join(SEARCH_TERMS), case=False, na=False)
            relevant_ids = labels[mask]["INDICATOR_ID"].tolist()
            print(f"  Found {len(relevant_ids)} relevant education indicators.")

        # Stream data and filter
        kept = []
        with zf.open(TARGET_CSV) as f:
            for chunk in pd.read_csv(f, chunksize=100_000, low_memory=False):
                mask = (
                    chunk["INDICATOR_ID"].isin(relevant_ids) &
                    chunk["YEAR"].between(START_YEAR, END_YEAR)
                )
                filtered = chunk[mask]
                if not filtered.empty:
                    kept.append(filtered)
        
        df = pd.concat(kept, ignore_index=True)
        # Merge labels back in
        return df.merge(labels, on="INDICATOR_ID", how="left")

if __name__ == "__main__":
    df = fetch_and_filter()

    if df.empty:
        print("⚠ No data matched filters")
    else:
        # Rename for consistency with our previous workflow
        df = df.rename(columns={
            "INDICATOR_ID": "INDICATOR_ID", 
            "INDICATOR_LABEL_EN": "INDICATOR_NAME",
            "COUNTRY_ID": "COUNTRY_ID",
            "YEAR": "YEAR",
            "VALUE": "VALUE"
        })
        
        # Save per country
        countries = df["COUNTRY_ID"].unique()
        for iso3 in countries:
            subset = df[df["COUNTRY_ID"] == iso3]
            out = OUT_DIR / f"opri_{iso3}.csv"
            subset.to_csv(out, index=False)
            print(f"✓ {iso3}: {len(subset):,} rows → {out}")
