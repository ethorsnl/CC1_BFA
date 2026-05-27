import json
import pandas as pd
import os
from pathlib import Path

def merge_notes():
    iso3 = os.environ.get("PIPELINE_ISO3", "BFA")
    geojson_path = Path(f"artifacts/{iso3}/schools.geojson")
    notes_path = Path("data/raw/field_observations.csv")
    
    if not geojson_path.exists():
        print(f"Error: {geojson_path} not found.")
        return
        
    if not notes_path.exists():
        print(f"Warning: {notes_path} not found. Skipping merge.")
        return

    # Load GeoJSON
    with open(geojson_path, 'r') as f:
        data = json.load(f)
        
    # Load Notes and handle nulls
    notes_df = pd.read_csv(notes_path)
    # Drop rows without coordinates as they can't be mapped/matched
    notes_df = notes_df.dropna(subset=['latitude', 'longitude'])
    # Fill other missing values with defaults
    notes_df['status'] = notes_df['status'].fillna("Unknown").str.strip().str.title()
    notes_df['note'] = notes_df['note'].fillna("No description provided.")
    notes_df['observer'] = notes_df['observer'].fillna("Anonymous")
    notes_df['observation_date'] = notes_df['observation_date'].fillna("Date unknown")
    notes_df['school_name'] = notes_df['school_name'].fillna("Unnamed School")
    
    # Create coordinate-based lookup
    # Key: (round(lat, 4), round(lon, 4))
    coord_lookup = {}
    for _, row in notes_df.iterrows():
        try:
            key = (round(float(row['latitude']), 4), round(float(row['longitude']), 4))
            coord_lookup[key] = row.to_dict()
        except (ValueError, TypeError):
            continue
    
    # Create name-based lookup as fallback
    name_lookup = notes_df.set_index('school_name').to_dict('index')
    
    matched_notes = set() # Track which notes from CSV were matched
    
    count_updated = 0
    for feature in data['features']:
        props = feature['properties']
        geom = feature['geometry']
        
        if geom['type'] != 'Point':
            continue
            
        lon, lat = geom['coordinates']
        coord_key = (round(float(lat), 4), round(float(lon), 4))
        name = props.get('name')
        
        note_data = None
        note_id = None
        
        # Priority 1: Exact Coordinate Match
        if coord_key in coord_lookup:
            note_data = coord_lookup[coord_key]
            note_id = coord_key
        # Priority 2: Name Match
        elif name and name not in [None, "null", "Ecole", "Unnamed Institution"] and name in name_lookup:
            note_data = name_lookup[name]
            note_id = name
            
        if note_data:
            props['field_status'] = note_data['status']
            props['field_note'] = note_data['note']
            props['field_date'] = note_data['observation_date']
            props['field_observer'] = note_data['observer']
            props['verification_status'] = props.get('verification_status', 'verified' if not props.get('is_discovery') else 'pending')
            
            if not name or name in ["null", "Ecole", "Unnamed Institution"]:
                props['name'] = note_data['school_name']
            
            matched_notes.add(note_id)
            count_updated += 1
            
    # Discovery Logic: Add new features for unmatched notes
    count_discovered = 0
    for _, row in notes_df.iterrows():
        coord_key = (round(float(row['latitude']), 4), round(float(row['longitude']), 4))
        name_key = row['school_name']
        
        if coord_key not in matched_notes and name_key not in matched_notes:
            # This is a new discovery
            new_feature = {
                "type": "Feature",
                "properties": {
                    "name": row['school_name'],
                    "amenity": "school",
                    "field_status": row['status'],
                    "field_note": row['note'],
                    "field_date": row['observation_date'],
                    "field_observer": row['observer'],
                    "is_discovery": True,
                    "verification_status": "pending"
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(float(row['longitude']), 4), round(float(row['latitude']), 4)]
                }
            }
            data['features'].append(new_feature)
            matched_notes.add(coord_key) # Avoid adding same discovery twice if data is messy
            count_discovered += 1
            
    # Save back
    with open(geojson_path, 'w') as f:
        json.dump(data, f)
        
    print(f"Successfully processed field notes:")
    print(f"  → {count_updated} existing schools updated.")
    print(f"  → {count_discovered} new schools discovered and added.")

if __name__ == "__main__":
    merge_notes()
