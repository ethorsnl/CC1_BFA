"""
01_fetch_acled_hdx.py
=====================
Downloads the latest ACLED data from the Humanitarian Data Exchange (HDX).
Replaces the API-based approach due to authentication issues.

Output: data/raw/acled/acled_latest.xlsx
"""

import requests
from pathlib import Path
from datetime import datetime

OUT_DIR = Path("data/raw/acled")
OUT_DIR.mkdir(parents=True, exist_ok=True)

DATASET_ID = "political-violence-events-and-fatalities"
HDX_API_URL = f"https://data.humdata.org/api/3/action/package_show?id={DATASET_ID}"

def download_latest_hdx():
    print(f"Fetching latest HDX dataset: {DATASET_ID}")
    
    # ── Large Download Warning ────────────────────────────────────────────────
    print("\n" + "!"*60)
    print("⚠️  WARNING: LARGE DOWNLOAD")
    print("The global ACLED conflict archive is typically 50MB-100MB.")
    print("This may take a moment depending on your connection.")
    print("!"*60 + "\n")

    response = requests.get(HDX_API_URL).json()
    
    if not response.get("success"):
        print("Failed to reach HDX API")
        return

    resources = response["result"]["resources"]
    # Look for the main XLSX data file
    xlsx_resource = next((res for res in resources if res['format'].lower() == 'xlsx'), None)
    
    if not xlsx_resource:
        print("No XLSX resource found.")
        return

    download_url = xlsx_resource["url"]
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"acled_{date_str}.xlsx"
    out_path = OUT_DIR / filename
    
    print(f"Downloading from: {download_url}")
    with requests.get(download_url, stream=True) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
    
    print(f"✓ Saved to {out_path}")
    
    # Verify integrity
    try:
        import pandas as pd
        df = pd.read_excel(out_path, nrows=1)
        print("✓ Integrity check passed: File is a valid Excel archive.")
    except Exception as e:
        print(f"❌ Integrity check failed: {e}")

if __name__ == "__main__":
    download_latest_hdx()
