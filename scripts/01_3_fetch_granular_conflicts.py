import os
import pandas as pd
import requests
from pathlib import Path

ISO3 = os.environ.get("PIPELINE_ISO3", "NGA")
# UCDP Country Codes: Nigeria = 475, Burkina Faso = 439
COUNTRY_CODES = {
    "NGA": 475,
    "BFA": 439
}

def fetch_granular():
    print(f"🚀 Fetching granular UCDP data for {ISO3}...")
    
    code = COUNTRY_CODES.get(ISO3)
    if not code:
        print(f"  ⚠ No UCDP country code known for {ISO3}. Skipping fetch.")
        return

    raw_dir = Path("data/raw/conflicts")
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / f"{ISO3}_granular_conflicts.csv"

    all_events = []
    page = 0
    
    try:
        while True:
            # UCDP GED API (v24.1 is current as of mid-2024)
            url = f"https://ucdpapi.prio.org/api/gedevents/24.1?pagesize=1000&pagenum={page}&country={code}"
            print(f"  → Fetching page {page}...")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            events = data.get("Result", [])
            if not events:
                break
            
            all_events.extend(events)
            page += 1
            
            # Safety break to avoid infinite loops if API behaves unexpectedly
            if page > 50: break 

        if all_events:
            df = pd.DataFrame(all_events)
            # Normalize column names to match what export script expects
            # adm_2, year, date_start, latitude, longitude, best
            df.to_csv(target, index=False)
            print(f"  ✓ Saved {len(df)} granular events to {target}")
        else:
            print(f"  ⚠ No events found for {ISO3} in UCDP.")

    except Exception as e:
        print(f"  ✗ Failed to fetch from UCDP: {e}")
        # Ensure an empty-ish file exists so downstream doesn't crash
        if not target.exists():
            pd.DataFrame(columns=['id', 'year', 'date_start', 'latitude', 'longitude', 'best', 'adm_1', 'adm_2']).to_csv(target, index=False)

if __name__ == "__main__":
    fetch_granular()
