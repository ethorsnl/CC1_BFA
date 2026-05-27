import pandas as pd
import os
import glob
import json

# Set path to the directory containing ACLED files
acled_dir = 'data/raw/acled/'

# Find most recent xlsx file
list_of_files = glob.glob(os.path.join(acled_dir, '*.xlsx'))
most_recent_file = max(list_of_files, key=os.path.getmtime) if list_of_files else None

# Define input path: use most recent if no hardcoded path provided
excel_file_path = most_recent_file if most_recent_file else 'data/raw/acled/acled_2026-05-21.xlsx'
output_dir = 'data/raw/acled/split' # directory where the CSVs will be saved

print(f"Using input file: {excel_file_path}")

# Create the output directory if it doesn't exist
os.makedirs(output_dir, exist_ok=True)

manifest = {}

try:
    print(f"Reading Excel file: {excel_file_path}")
    xls = pd.ExcelFile(excel_file_path)

    for target_sheet_name in xls.sheet_names:
        print(f"Processing sheet: {target_sheet_name}")
        df = xls.parse(target_sheet_name)

        # Sanitize sheet name for use as a filename
        safe_sheet_name = "".join([c if c.isalnum() or c in (' ', '_') else '_' for c in target_sheet_name]).rstrip()
        safe_sheet_name = '_'.join(filter(None, safe_sheet_name.split('_')))
        
        csv_filename = f"{safe_sheet_name}.csv"
        output_file_path = os.path.join(output_dir, csv_filename)
        df.to_csv(output_file_path, index=False)
        print(f"  ✓ Saved {output_file_path}")

        # Create Markdown file with unique country names
        if 'Country' in df.columns:
            countries = sorted(df['Country'].unique().astype(str))
            
            # Update manifest
            for country in countries:
                if country not in manifest:
                    manifest[country] = []
                manifest[country].append(csv_filename)

            md_file_path = os.path.join(output_dir, f"{safe_sheet_name}_countries.md")
            with open(md_file_path, "w") as f:
                f.write(f"# Countries in {target_sheet_name}\n\n")
                for country in countries:
                    f.write(f"- {country}\n")
            print(f"  ✓ Saved country list to {md_file_path}")
        else:
            print(f"  ! 'Country' column not found in {target_sheet_name}. Skipping .md generation.")

    # Save manifest.json
    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=4)
    print(f"\n✓ Saved master manifest to {manifest_path}")

    print("\nFinished processing all sheets.")

except FileNotFoundError:
    print(f"Error: The file {excel_file_path} was not found.")
except ImportError:
    print("Error: The 'pandas' library is not installed. Please install it using 'pip install pandas openpyxl'.")
except Exception as e:
    print(f"An error occurred: {e}")
