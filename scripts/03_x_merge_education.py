import pandas as pd
from pathlib import Path
import glob

def merge_education_data(iso3_list=None):
    raw_dir = Path("data/raw/education")
    out_dir = Path("data/clean/education")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    all_data = []
    
    # If no iso3_list, find all ISO3s in the directory
    if not iso3_list:
        files = glob.glob(str(raw_dir / "*.csv"))
        iso3_list = list(set([f.split("_")[-1].replace(".csv", "") for f in files]))

    for iso3 in iso3_list:
        print(f"Processing {iso3}...")
        
        # 1. Process World Bank (National)
        wb_file = raw_dir / f"worldbank_national_{iso3}.csv"
        if wb_file.exists():
            df_wb = pd.read_csv(wb_file)
            df_wb_clean = pd.DataFrame({
                "iso3":      df_wb["iso3"],
                "year":      df_wb["year"],
                "region":    "National",
                "indicator": df_wb["indicator_name"],
                "value":     df_wb["value"],
                "source":    "WorldBank"
            })
            all_data.append(df_wb_clean)
            print(f"  + Added World Bank ({len(df_wb_clean)} rows)")

        # 2. Process DHS (Subnational)
        dhs_file = raw_dir / f"dhs_subnational_{iso3}.csv"
        if dhs_file.exists():
            df_dhs = pd.read_csv(dhs_file)
            # Standardize column names
            df_dhs_clean = pd.DataFrame({
                "iso3":      df_dhs["iso3"],
                "year":      df_dhs["SurveyYear"],
                "region":    df_dhs["CharacteristicLabel"],
                "indicator": df_dhs["Indicator"],
                "value":     df_dhs["Value"],
                "source":    "DHS"
            })
            all_data.append(df_dhs_clean)
            print(f"  + Added DHS ({len(df_dhs_clean)} rows)")

        # 3. Process HDX (Historical/Comprehensive)
        hdx_file = raw_dir / f"hdx_worldbank_education_{iso3}.csv"
        if hdx_file.exists():
            df_hdx = pd.read_csv(hdx_file)
            df_hdx_clean = pd.DataFrame({
                "iso3":      df_hdx["iso3"],
                "year":      df_hdx["Year"],
                "region":    "National",
                "indicator": df_hdx["Indicator Name"],
                "value":     df_hdx["Value"],
                "source":    "HDX-WB"
            })
            all_data.append(df_hdx_clean)
            print(f"  + Added HDX-WB ({len(df_hdx_clean)} rows)")

        # 4. Process OPRI (UNESCO/National)
        opri_file = raw_dir / "opri" / f"opri_{iso3}.csv"
        if opri_file.exists():
            df_opri = pd.read_csv(opri_file)
            
            # Now using the INDICATOR_NAME column directly from the new fetcher
            df_opri_clean = pd.DataFrame({
                "iso3":      df_opri["COUNTRY_ID"],
                "year":      df_opri["YEAR"],
                "region":    "National",
                "indicator": df_opri["INDICATOR_NAME"],
                "value":     df_opri["VALUE"],
                "source":    "OPRI"
            })
            all_data.append(df_opri_clean)
            print(f"  + Added OPRI ({len(df_opri_clean)} rows)")

    if not all_data:
        print("No data found to merge.")
        return

    # Combine everything
    master_df = pd.concat(all_data, ignore_index=True)

    # 5. Hierarchical Deduplication
    # Define source priority (lower number = higher priority)
    priority = {"DHS": 1, "OPRI": 2, "WorldBank": 3, "HDX-WB": 4}
    master_df['priority'] = master_df['source'].map(priority).fillna(99)
    
    # Sort and drop duplicates, keeping the highest priority source
    master_df = master_df.sort_values(['iso3', 'indicator', 'year', 'region', 'priority'])
    master_df = master_df.drop_duplicates(subset=['iso3', 'indicator', 'year', 'region'], keep='first')
    master_df = master_df.drop(columns=['priority'])
    
    # Round values to 2 decimal places
    master_df["value"] = master_df["value"].round(2)
    
    # Sort for usability
    master_df = master_df.sort_values(["iso3", "indicator", "year", "region"])
    
    # Save
    out_path = out_dir / "master_education.csv"
    master_df.to_csv(out_path, index=False)
    print(f"\n✅ Created master education file: {out_path}")

    # Auto-generate README
    print("Generating README documentation...")
    summary = master_df.groupby('indicator')['year'].agg(['min', 'max', 'count']).reset_index()
    with open(out_dir / 'README.md', 'w') as f:
        f.write('# Education Dataset Documentation\n\n')
        f.write('This directory contains the master education dataset compiled from DHS, World Bank, HDX-WB, and OPRI sources.\n\n')
        f.write('## Indicators and Coverage\n\n')
        f.write('| Indicator | First Year | Last Year | Data Points |\n')
        f.write('| :--- | :--- | :--- | :--- |\n')
        for _, row in summary.iterrows():
            f.write(f'| {row["indicator"]} | {int(row["min"])} | {int(row["max"])} | {int(row["count"])} |\n')
    
    print(f"Total rows: {len(master_df):,}")
    print("\nSample Data:")
    print(master_df.head(10).to_string(index=False))

if __name__ == "__main__":
    merge_education_data()
