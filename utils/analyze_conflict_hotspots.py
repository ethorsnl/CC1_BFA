import pandas as pd
import argparse
from pathlib import Path

def analyze_hotspots(file_path, year=None, month=None, top_n=10, level='admin2'):
    """
    Groups geocoded data by Admin1 or Admin2 to identify conflict hotspots.
    Saves the full result to utils/conflict_hotspots and prints a summary.
    """
    if not Path(file_path).exists():
        print(f"Error: File {file_path} not found.")
        return

    df = pd.read_csv(file_path)
    country_name = Path(file_path).stem.replace("_geocoded", "")
    out_dir = Path("utils/conflict_hotspots")
    out_dir.mkdir(exist_ok=True)

    # Determine behavior based on args
    is_default = not year and not month
    
    group_cols = ['Admin1'] if level == 'admin1' else ['Admin1', 'Admin2']
    level_tag = level.lower()
    
    if is_default:
        # Default: most recent 3 years
        max_year = df['Year'].max()
        years = [max_year, max_year-1, max_year-2]
        df_filtered = df[df['Year'].isin(years)].copy()
        time_tag = f"recent_3_years_{min(years)}_{max(years)}"
        print(f"Default Mode: Analyzing most recent 3 years ({min(years)}-{max(years)}) at {level_tag} level, saving to CSV.")
    else:
        df_filtered = df.copy()
        if year:
            df_filtered = df_filtered[df_filtered['Year'] == int(year)]
        if month:
            df_filtered = df_filtered[df_filtered['Month'].str.lower() == month.lower()]
        
        y_str = f"_{year}" if year else "_all_years"
        m_str = f"_{month}" if month else "_all_months"
        time_tag = f"{y_str}{m_str}".lstrip("_")

    if df_filtered.empty:
        print(f"No data found for the specified period.")
        return

    # Group and aggregate
    hotspots = df_filtered.groupby(group_cols).agg({
        'Events': 'sum',
        'Fatalities': 'sum'
    }).reset_index()

    # Sort by Fatalities, then Events
    hotspots = hotspots.sort_values(by=['Fatalities', 'Events'], ascending=False)

    # Save to CSV (All records)
    out_filename = f"hotspots_{country_name}_{level_tag}_{time_tag}.csv"
    out_path = out_dir / out_filename
    hotspots.to_csv(out_path, index=False)
    
    print(f"\n--- Hotspot Analysis: {country_name} ---")
    print(f"Data ({level_tag} level) saved to: {out_path}")
    
    print(f"\nTop {top_n} Hotspots ({level_tag} level):")
    print(hotspots.head(top_n).to_string(index=False))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Identify conflict hotspots from geocoded ACLED data.")
    parser.add_argument("--file", default="data/clean/acled/HRP_2_countries/Burkina_Faso_geocoded.csv", help="Path to the geocoded CSV file")
    parser.add_argument("--year", type=int, help="Filter by year")
    parser.add_argument("--month", help="Filter by month (e.g., January)")
    parser.add_argument("--top", type=int, default=10, help="Number of top hotspots to show in terminal")
    parser.add_argument("--level", choices=['admin1', 'admin2'], default='admin2', help="Administrative level to aggregate by")

    args = parser.parse_args()

    analyze_hotspots(args.file, args.year, args.month, args.top, args.level)

