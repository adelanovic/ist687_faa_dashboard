# This file contains the prompt that was used with Anthropics claude.ai to generate the streamlist dashboard for exploring FAA Wildlife Strike database.

# Prompt: FAA Wildlife Strike Dashboard (data prep + dashboard foundation)

Copy everything below the line into any capable LLM. It is self-contained: it
carries the dataset schema, the real distributions, and the specific traps that
were discovered the hard way, so the model does not have to rediscover them.

---

## TASK

Build the data-preparation script and the foundation of a Streamlit dashboard
for the FAA Wildlife Strike Database. Produce three Python files: `prep_data.py`,
`core.py`, and `app.py`. Write complete, runnable code with no placeholders, no
`# TODO` markers, and no truncated functions.

## CONTEXT

The FAA Wildlife Strike Database records bird and wildlife collisions with
aircraft, reported voluntarily by pilots, airports, and airlines. The user has
downloaded the full export from https://wildlife.faa.gov/search as one or more
Excel files. This is for a graduate data-science course project, so the code
must be readable and the analytical caveats must be surfaced in the UI rather
than buried.

Scale: **~352,000 rows × 102 columns**, covering **1990–2026**, across **~2,800
airports** and **~974 species labels**. The overall damage rate is **6.3%**.

## DATASET SCHEMA

The export has 102 columns. Keep only these 36; the rest are fully redacted,
near-100% empty, or per-export administrative noise:

```
INDEX_NR, INCIDENT_DATE, INCIDENT_MONTH, INCIDENT_YEAR, TIME_OF_DAY,
AIRPORT_ID, AIRPORT, LATITUDE, LONGITUDE, STATE, FAAREGION,
OPERATOR, AIRCRAFT, AC_CLASS, AC_MASS, TYPE_ENG, NUM_ENGS,
PHASE_OF_FLIGHT, HEIGHT, SPEED, DISTANCE, SKY, PRECIPITATION, AOS,
COST_REPAIRS_INFL_ADJ, COST_OTHER_INFL_ADJ,
INDICATED_DAMAGE, DAMAGE_LEVEL, EFFECT,
SPECIES, SIZE, NUM_SEEN, NUM_STRUCK, WARNED,
NR_INJURIES, NR_FATALITIES, REMARKS
```

Explicitly drop: `NR_FATALITIES` and `NR_INJURIES` (>99.8% missing),
`ENROUTE_STATE` (98.3% missing), `BIRD_BAND_NUMBER`, `IMAGE`, `TRANSFER`,
`LUPDATE`, `REPORTED_NAME` and `REPORTED_TITLE` (both redacted to the literal
string `"REDACTED"`), and all 40+ `STR_*` / `DAM_*` / `ING_*` per-component
strike flags.

Key value domains:

- `INDICATED_DAMAGE`: 0 or 1. Never missing. This is the reliable damage flag.
- `DAMAGE_LEVEL`: `N`, `M`, `M?`, `S`, `D` for none / minor / uncertain-minor /
  substantial / destroyed. Blank on ~88% of rows.
- `SIZE`: `Small`, `Medium`, `Large`.
- `PHASE_OF_FLIGHT`: `Parked`, `Taxi`, `Take-off Run`, `Climb`, `Departure`,
  `En Route`, `Descent`, `Approach`, `Arrival`, `Landing Roll`, `Local`.
- `TIME_OF_DAY`: `Dawn`, `Day`, `Dusk`, `Night`.
- `SKY`: `No Cloud`, `Some Cloud`, `Overcast`.
- `PRECIPITATION`: comma-joined multi-values such as `"Fog, Rain"`.
- `AC_MASS`: ordinal 1–5 (weight class, 5 heaviest).
- `SPECIES`: free-ish text, includes `"Unknown bird - small/medium/large"`.

## DATA TRAPS: HANDLE ALL OF THESE

These were found by profiling the real file. Each one silently produces wrong
output if missed.

**1. `DAMAGE_LEVEL` is structurally missing, not missing-at-random.**
Every blank corresponds exactly to `INDICATED_DAMAGE == 0`, because reporters
skipped the severity box when nothing was damaged. Verified by crosstab: zero
exceptions. Fill blanks with a real "no damage" category. Do **not** impute it,
and do **not** drop those rows.

**2. Never use the literal string `"None"` as a category label.**
`pandas.read_csv` parses `"None"` back as `NaN`, which silently deletes those
rows from every groupby after a CSV round-trip. In testing this erased 88% of
records from the severity breakdown and made large birds appear to have a 100%
damage rate. Use `"No damage"`, `"None reported"`, and `"No effect"` for
`DAMAGE_LEVEL`, `PRECIPITATION`, and `EFFECT` respectively. Additionally,
re-fill these columns defensively after loading, in case the data came back
through CSV.

**3. Coordinates are airport-level, not incident-level.**
~2,800 unique lat/lon pairs serve all 352k records. Every strike at a given
airport carries that airport's coordinates. Plotting individual strikes just
stacks points. All mapping must aggregate to the airport. (Coordinates are
perfectly consistent within each airport, so a simple groupby works; no
reconciliation needed.)

**4. ~13.6% of rows have no coordinates.** Almost all are `AIRPORT == "UNKNOWN"`
(en-route strikes with no fixed location), plus a few `PRIVATE AIR STRIP`,
`REMOTE_WATER`, `OIL RIG`. Exclude from maps, keep in every non-spatial
analysis, and expose the coverage percentage in the UI.

**5. Strike volume is confounded with traffic volume.** The busiest airports
lead on raw counts because they are busy, not because they are hazardous. The
dashboard must offer a **damage rate** metric (damaging strikes divided by all
strikes), which conditions on a strike already occurring so airport size largely
cancels out. Show a visible warning on the raw-volume view.

**6. The long-run upward trend is partly administrative.** Reporting is
voluntary and participation grew substantially over the covered period. Overall
damage rate falls from 12.1% (1990–2005) to 6.3% (full range) not because
strikes got safer but because the later, larger pool of reports skews minor.
Surface this as a caption on any trend chart.

**7. Export parts can overlap.** De-duplicate on `INDEX_NR` after concatenating.

**8. `STATE` contains territories and foreign codes.** Filter to the 50 states
plus DC before building a `USA-states` choropleth, or the extra codes stretch
the color scale.

**9. Nullable `Int64` breaks Plotly.** `value_counts().reindex(range(1,13),
fill_value=0)` on a nullable integer column yields `pd.NA`, which Plotly cannot
serialize. Cast to plain `int`.

**10. ~57% of records are `"Unknown bird"`.** Flag identified vs unidentified
species so users can filter, and never present species statistics without
making that share visible.

## ARCHITECTURE

Three files, with a hard separation:

- **`prep_data.py`**: reads every `.xlsx`/`.csv` in `data/raw/`, de-dupes,
  cleans, writes one `data/strikes.parquet`. Falls back to `strikes.csv.gz` if
  pyarrow is unavailable. Run once; not imported by the app.
- **`core.py`**: all filtering and aggregation, **pure pandas with no
  Streamlit import**. Must be importable from a Jupyter notebook so a number in
  a report cannot drift from a number on screen.
- **`app.py`**: Streamlit UI only. Imports `core`. Contains no aggregation
  logic of its own.

## SPEC: `prep_data.py`

- Glob `data/raw/*.xlsx` and `data/raw/*.csv`; concatenate; de-dupe on
  `INDEX_NR`; print per-file row counts as it goes (the Excel read takes ~105
  seconds per 100k rows, so progress output matters).
- Exit with a clear message if `data/raw/` is empty.
- Warn if a listed keep-column is absent from the export.
- Apply all the cleaning in the traps section above.
- Coerce numerics with `errors="coerce"`; parse dates; null out impossible
  coordinates (|lat| > 90, |lon| > 180, exact 0/0).
- Add derived columns: `HAS_COORDS`, `PHASE_GROUP` (Ground / Terminal /
  Airborne / Unrecorded), `SPECIES_KNOWN`, `COST_TOTAL` (repairs + other,
  inflation-adjusted, NaN treated as 0), `HEIGHT_BAND`
  (`Ground (0 ft)` / `1–500 ft` / `501–3,500 ft` / `>3,500 ft`).
- For `TIME_OF_DAY`, `PHASE_OF_FLIGHT`, `SKY`, `SIZE`, `AC_CLASS`: fill missing
  with an explicit `"Unrecorded"` level. Missingness here is informative, so do
  not impute and do not drop.
- Print a summary: rows, columns, year range, airport count, coordinate
  coverage, damage rate, species count.
- **Print a warning if the final years are far sparser than the peak year.** A
  truncated download looks exactly like a collapse in strikes, and users will
  otherwise report a false declining trend.

## SPEC: `core.py`

Pure functions, each independently testable:

- `load(path=None)`: read parquet or csv.gz, restore dtypes, re-apply the
  defensive `"None"`-collision fills, rebuild the ordered `DAMAGE_LEVEL`
  categorical.
- `options(df)`: derive filter menu contents from the data, not hard-coded.
- `apply_filters(df, ...)`: boolean-mask filter; every argument optional,
  `None` meaning no filter. Params: years tuple, months, states, sizes,
  phase_groups, phases, times, species, damage_levels, damage_only flag,
  known_species_only flag.
- `airport_summary(df, min_strikes=1)`: one row per mappable airport with
  lat, lon, strikes, damaging, damage_rate, cost_total, cost_per_strike,
  pct_large, pct_night, top_species, top_phase, first_year, last_year.
  Exclude non-airport locations (`Unknown`, `Private Air Strip`,
  `Remote_Water`, `Oil Rig`).
- `state_summary(df)`, `species_summary(df, min_strikes=20)`,
  `monthly_series(df)`, `seasonality(df)`, `kpis(df)`.
- `color_ramp(values, ...)`: map values to RGB triples for pydeck, viridis-like,
  implemented inline so the app needs no matplotlib.

**Every aggregation must return an empty DataFrame with the correct column
schema when the input is empty**, not raise. Filter combinations that match
nothing are normal user behavior.

## SPEC: `app.py`

Wide layout. Sidebar filters apply to all tabs simultaneously: year range
slider, months, states, sizes, phase group, phase, time of day, species
multiselect, damaging-only checkbox, identified-species-only checkbox, and a
minimum-strikes-per-airport threshold for the map.

Header row of metrics: strikes, damaging, damage rate, airports, reported cost,
species count.

Five tabs:

1. **Map**: pydeck `ScatterplotLayer`, one point per airport. Point **area**
   (radius ∝ √count) encodes volume; color is user-switchable between strike
   volume, damage rate, cost per strike, and % large birds. Log-scale the color
   ramp for the long-tailed volume and cost metrics, linear for the bounded
   rates. Rich HTML tooltip. Below it, a Plotly `USA-states` choropleth.
2. **Airport detail**: select an airport; show its metrics, top species bar
   chart, month profile, phase breakdown, and recent reports including the
   free-text `REMARKS`.
3. **Species**: frequency (log x) against damage rate, bubble size by total
   cost, colored by bird size. Plus a sortable table.
4. **Trends**: monthly counts, seasonality, damage rate by phase, severity mix
   by bird size, with the reporting-artifact caption.
5. **Records**: filtered table plus a CSV download button.

## PERFORMANCE REQUIREMENT

Measured at ~340k rows: filtering takes ~170 ms, airport aggregation ~590 ms,
and the full set of aggregations ~1.06 s combined. Streamlit reruns the entire
script on every widget interaction, so an uncached app lags visibly on every
checkbox click.

**Cache the aggregations on the filter values, never on the DataFrame**, because
hashing 350k rows costs more than the work it saves. Build a hashable params
tuple from the sidebar state and key `@st.cache_data` functions on it. Structure
it so that changing only the min-strikes threshold re-aggregates without
re-running the row filter.

**Cached frames must not be mutated in place.** Streamlit returns the cached
object itself, so `.copy()` before assigning derived columns or the cache
entry is corrupted for every subsequent rerun.

Also cache the initial data load, and use `@st.cache_data` rather than
`@st.cache_resource` for DataFrames.

## STYLE

- Comment the *why*, not the *what*. Every non-obvious line should say what goes
  wrong without it. The traps above are the comments worth writing.
- No emoji in code. No decorative section banners beyond simple separators.
- Prefer explicit column lists over `select_dtypes` guessing.
- Guard every user-facing path against the empty-filter case with a friendly
  message rather than a traceback.

## ACCEPTANCE CHECKS

State these results after generating the code so they can be verified:

- Loading the prepped file yields **zero nulls in `DAMAGE_LEVEL`**.
- The severity mix for `SIZE == "Large"` includes a substantial "No damage"
  share of roughly 54%. If it shows 0%, trap #2 was missed.
- `state_summary` returns **at most 51 rows**.
- Filtering to a nonexistent state returns empty frames from every aggregation
  without raising.
- Damage rate by size increases monotonically Small → Medium → Large.
- Damage rate by phase is lowest for `Parked`/`Taxi` and highest for `En Route`.

Also provide `requirements.txt` (streamlit, pandas, numpy, plotly, pydeck,
openpyxl, pyarrow), a `.gitignore` that excludes `data/raw/` and `*.xlsx`
because the raw export is ~27 MB per part, and a `README.md` covering setup,
the three data caveats, and deployment.

## DEPLOYMENT NOTE

GitHub Pages cannot host this, because it serves static files only and Streamlit
needs a live Python process. Recommend Streamlit Community Cloud, which deploys
directly from a GitHub repo. Note that since `data/raw/` is gitignored, the
prepped parquet must be committed for the hosted app to have data.
