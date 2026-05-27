# Methodology

## What this measures

A composite vulnerability score per admin1 region per year, combining conflict intensity and education continuity indicators. High scores identify regions where EBI should prioritise resources.

## Indicators and scoring

Each indicator is min-max normalised to [0, 1] within each year. The composite is an unweighted mean across available indicators (`skipna=True` — honest about data gaps).

| # | Indicator | Source | Direction |
|---|-----------|--------|-----------|
| 1 | Conflict event count per admin1 | ACLED | Higher = more vulnerable |
| 2 | Conflict fatalities per admin1 | ACLED | Higher = more vulnerable |
| 3 | Inverse of primary survival rate | UNESCO UIS OPRI (`SURVCOMP.PT4`) | Lower survival = more vulnerable |
| 4 | Out-of-school rate, primary | UNESCO UIS OPRI (`ROFST.1.cp`) | Higher = more vulnerable |

Each admin1 carries a `score_basis` flag (e.g. `3/4_indicators`) showing how many indicators contributed to its score. Regions missing education data are scored on conflict indicators only — they are not penalised or imputed to a worst-case value.

Final scores are tercile-classified into **high / medium / lower priority**.

## Data sources

| Source | Dataset | Coverage | Licence |
|--------|---------|----------|---------|
| ACLED | Armed conflict events by location | 1997–present, weekly updates | CC-BY-4.0, free for non-commercial |
| UNESCO UIS | OPRI — Other Policy Relevant Indicators | 2000–2023, national level | CC-BY-SA-3.0-IGO |
| OCHA | COD Admin Boundaries | Country-specific | CC-BY-IGO-3.0 |

## Known limitations

**Education data is national-level only.** UNESCO UIS OPRI does not publish admin1-disaggregated survival rates or OOS rates for most conflict-affected countries. Indicators 3 and 4 therefore apply the same national figure to every admin1 region in a given year. This is a known limitation — it means the ranking between regions in a single year is driven primarily by ACLED conflict data. Subnational education data from DHS/MICS surveys (where available) would improve this.

**ACLED coverage varies by country.** For some countries and time periods, ACLED coverage is denser in urban or accessible areas, which may undercount conflict in remote regions.

**Survival rate lags.** UNESCO OPRI education data typically lags 2–3 years behind the current year due to reporting cycles. The most recent year of education data available for many countries is 2021 or 2022.

## Transferability

The pipeline is country-agnostic. To run on a different country:
1. Change `ISO3` at the top of each script (or pass `--iso3 MLI` to `run_all.py`)
2. Ensure the country is covered by ACLED (most LMICs from 1997)
3. Check UNESCO UIS coverage for your country at [data.uis.unesco.org](https://data.uis.unesco.org)
4. Download COD boundaries from [HDX](https://data.humdata.org/dataset/cod-ab-{country-slug})
