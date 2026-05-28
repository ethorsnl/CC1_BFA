# Education Risk Pipeline: Technical Manual

This project provides a robust, 24-step data pipeline to compute the Education Vulnerability Index (EVI). It is fully generalized to support any ISO3 country code.

---

## 🛠 Setup & Prerequisites

### Environment
- **Python 3.9+** is required.
- **Dependencies**: Install all required libraries via pip:
  ```bash
  pip install -r requirements.txt
  ```
- **API Keys**: No API keys are required for the standard fetchers. The pipeline uses public data APIs (HDX, WorldBank, UNESCO, WorldPop). Geocoding uses the Nominatim API which requires only a valid User-Agent (already configured).
- **Disk Space**: The full pipeline for a single country requires ~500MB - 1GB of disk space, primarily due to global conflict archives and high-resolution population rasters.

---

## 🚀 Master Orchestrator: `run_all.py`

The `run_all.py` script sequences 24 steps across four phases. **The `#` column in the tables below corresponds directly to the numbers used in `--skip` and `--only` flags.**

### Usage Examples
```bash
# Run for a specific country (e.g., Mali)
python scripts/run_all.py --iso3 MLI

# Resume after a fetch failure (skip steps 1-8)
python scripts/run_all.py --iso3 NER --skip 1 2 3 4 5 6 7 8

# Run ONLY the final export and UI update
python scripts/run_all.py --iso3 BFA --only 22 23 24
```

---

## 🛠 Pipeline Catalog

### Phase 1: Data Acquisition (Fetch)
| # | Script | Description | Primary Output |
|---|---|---|---|
| 1 | `01_fetch_acled_hdx.py` | Global ACLED conflict events (⚠️ ~100MB) | `data/raw/acled/acled_*.xlsx` |
| 2 | `01_3_fetch_granular_conflicts.py` | UCDP point-level conflict data | `data/raw/conflicts/{ISO3}_granular.csv` |
| 3 | `02_fetch_schools_hdx.py` | Official school datasets from HDX | `data/raw/schools_hdx/{ISO3}_schools.geojson` |
| 4 | `02_fetch_schools.py` | Fallback OSM school locations | `data/raw/schools/schools_osm.geojson` |
| 5 | `03_fetch_education.py` | Subnational DHS & WB indicators | `data/raw/education/dhs_subnational_{ISO3}.csv` |
| 6 | `03_1_fetch_opri.py` | National UNESCO OPRI indicators | `data/raw/opri/opri_{ISO3}.csv` |
| 7 | `04_fetch_boundaries.py` | OCHA Administrative boundaries | `data/raw/boundaries/{ISO3}_admin2.geojson` |
| 8 | `04_1_fetch_worldpop.py` | Population density rasters (⚠️ 50MB-2GB+) | `data/clean/{ISO3}_pop_density/*.json` |

---

## 📦 Managing Large Downloads

Some steps involve significant data transfers. You can manage this by running scripts individually with granular flags:

### Granular WorldPop Fetching
Instead of fetching all years (2000-2020), fetch only the years needed for your analysis (e.g., recent years):
```bash
# Fetch only the latest year (2020)
python scripts/04_1_fetch_worldpop.py --iso3 BFA --years latest

# Fetch specific recent years
python scripts/04_1_fetch_worldpop.py --iso3 MLI --years 2018,2019,2020
```

### Automatic Cleanup
Use the `--clean` flag on Step 8 to delete the heavy `.tif` files after they have been processed into lightweight JSON heatmaps:
```bash
python scripts/04_1_fetch_worldpop.py --iso3 BFA --years all --clean
```

---

## 🛠 Pipeline Catalog (continued)

### Phase 2: Processing & Cleaning
| # | Script | Input | Key Action |
|---|---|---|---|
| 9 | `01_1_split_acled.py` | Global XLSX | Splits global file into country-sheet CSVs |
| 10 | `01_2_hrp_country.py` | Split CSVs | Extracts `{Country}.csv` + Geocoding |
| 11 | `02_2_merge_schools.py` | HDX + OSM | Merges multiple school sources |
| 12 | `02_x_clean_school.py` | Merged Schools | Spatial deduplication (50m buffer) |
| 13 | `03_x_merge_education.py` | DHS/WB/OPRI | Creates `data/clean/education/master_education.csv` |
| 14 | `04_x_align_admin_names.py` | ACLED + Boundaries | Creates `artifacts/admin_mapping.json` |
| 15 | `04_y_validate_data_integrity.py` | Processed Data | **Quality Gate**: Aborts on critical missing data |

### Phase 3: Analysis & Scoring
| # | Script | Output | Feature |
|---|---|---|---|
| 16 | `05_build_analysis.py` | `artifacts/{ISO3}_vulnerability.csv` | Baseline score + National trends |
| 17 | `05_1_calculate_hybrid_vulnerability.py`| `artifacts/{ISO3}_hybrid_index.csv` | Factors in population & schools |
| 18 | `06_1_calculate_school_proximity.py` | `artifacts/proximity_risk_stats.json` | Min-distance to active conflict |
| 19 | `06_2_school_fragility.py` | `artifacts/school_vulnerability_scores.json`| Individual school risk ranking |
| 20 | `06_3_calculate_density_gap.py` | `artifacts/province_school_fragility.csv` | School availability vs population |
| 21 | `06_4_aggregate_at_risk_schools.py` | `artifacts/province_at_risk_stats.json` | Time-series counts for dashboard |

### Phase 4: Export & Finalization
| # | Script | Output | Purpose |
|---|---|---|---|
| 22 | `06_export_map_data.py` | `artifacts/data.geojson` | Choropleth layers + Insights |
| 23 | `06_x_export_conflicts_geojson.py` | `artifacts/conflicts.geojson` | Combined ACLED + UCDP layer |
| 24 | `07_update_dashboard.py` | `index.html` | **UI Sync**: Updates country names/paths |
| 25 | `08_merge_field_notes.py` | `artifacts/schools.geojson` | **Ground Truth**: Merges qualitative field observations |

---

## 🔗 Key Data Dependencies

- **Step 14 (`admin_mapping.json`)** is consumed by steps 17, 21, and 22 to ensure names match between datasets and the map.
- **Step 13 (`master_education.csv`)** is the primary source for all education-related risk drivers in Phase 3.
- **Step 19 (`school_vulnerability_scores.json`)** is required for the aggregate statistics in Step 21.

---

## 🛡 Failsafe & Thresholds

### 1. The "Critical Chain" (Steps 1-15)
If any step in the Fetch or Processing phases fails, the pipeline **aborts**. This is intentional: you cannot perform a valid analysis on corrupted or missing boundary/conflict data.

### 2. Graceful Degradation in Scoring
The `05_build_analysis` script handles missing education indicators:
- **Minimum Floor**: If at least 1 indicator is found, a score is produced.
- **Basis Flag**: The output CSV includes a `score_basis` column (e.g., `1/3_indicators`) so analysts can judge the confidence of the score.
- **Missing All**: If 0 indicators are found for a country, the step will fail.

### 3. Source Fallback
`06_x` checks for UCDP data. If unavailable, it skips the granular layer and builds the map using ACLED only, rather than crashing.
