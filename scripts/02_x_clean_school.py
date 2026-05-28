import pandas as pd
import os
from pathlib import Path

ISO3 = os.environ.get("PIPELINE_ISO3", "BFA")

def clean_schools():
    print(f"🚀 Cleaning school data for {ISO3}...")
    src = Path("data/raw/schools/all_schools_combined.csv")
    dst = Path("data/clean/schools") / f"schools_{ISO3}.csv"
    
    if not src.exists():
        print(f"  ✗ Source file not found: {src}")
        return

    dst.parent.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(src)
    
    # Simple filtering if iso3 column exists
    if 'iso3' in df.columns:
        df = df[df['iso3'] == ISO3]
    
    df.to_csv(dst, index=False)
    print(f"  ✓ Saved cleaned schools for {ISO3} to {dst} ({len(df)} rows)")

if __name__ == "__main__":
    clean_schools()
