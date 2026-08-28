# FAA Wildlife Strike Explorer

An interactive map and dashboard for the FAA Wildlife Strike Database.
Python + Streamlit. Handles the full ~300k-record export comfortably.

## Setup

```bash
pip install -r requirements.txt
```

Download every part of the export from https://wildlife.faa.gov/search and put
the files in `data/raw/`. Both `.xlsx` and `.csv` work, and you can mix them.

```bash
python prep_data.py     # binds all parts, cleans, writes data/strikes.parquet
streamlit run app.py    # opens at http://localhost:8501
```

`prep_data.py` only needs re-running when you add new raw files.

## What's here

| File | Role |
|---|---|
| `prep_data.py` | Reads every part from `data/raw/`, de-duplicates on `INDEX_NR`, cleans, writes one compact file |
| `core.py` | All filtering and aggregation. Plain pandas, no Streamlit — importable from a notebook or testable directly |
| `app.py` | The Streamlit UI |

The split between `core.py` and `app.py` is deliberate: you can `import core`
in Jupyter and reuse the exact aggregations the dashboard shows, so a number in
a report can never drift from the number on screen.

## The five tabs

- **Map** — ~1,400 airports as points, area scaled to strike volume, colour switchable between volume, damage rate, cost per strike, and % large birds. Plus a state choropleth.
- **Airport detail** — drill into one airport: species mix, month profile, phase breakdown, and the underlying reports with pilot remarks.
- **Species** — frequency against severity, log-x. The upper-right quadrant is the operationally interesting set.
- **Trends** — monthly counts, seasonality, damage rate by flight phase, severity mix by bird size.
- **Records** — the filtered table, downloadable as CSV.

All sidebar filters apply to every tab at once.

## Three things about this data that shape the dashboard

**Coordinates are airport-level, not incident-level.** Roughly 1,400 unique
coordinate pairs serve all records — every O'Hare strike carries O'Hare's
latitude. Plotting individual strikes would just stack points. The map
therefore aggregates to airports, which is also what keeps it fast.

**Strike volume is confounded with traffic volume.** O'Hare leads on raw
counts because O'Hare is busy. The **damage rate** colour mode conditions on a
strike already having occurred, so airport size largely cancels out — that view
answers "where are strikes severe," which is usually the question you actually
have. The app shows a warning on the volume view for this reason.

**The upward trend is partly administrative.** FAA strike reporting is
voluntary and participation grew substantially over the covered period, so
year-over-year growth mixes real change with reporting-rate change. The
seasonal cycle (a strong Jul–Oct peak tracking fall migration) is the more
defensible signal. There is a note on the Trends tab.

`prep_data.py` also prints a warning if the last few years in your data are far
sparser than the peak — that means you are missing parts of the export, not
that strikes stopped.

## A note on the data cleaning

`DAMAGE_LEVEL` is blank on ~88% of records, but it is not missing at random:
every blank corresponds exactly to `INDICATED_DAMAGE = 0`. Reporters skipped
the severity box when there was nothing to describe. The prep script fills
these as a real category rather than imputing them.

That category is labelled `"No damage"`, not `"None"`, because `pandas.read_csv`
parses the literal string `"None"` back as `NaN` — which silently drops those
records from every severity breakdown after a round-trip. Same reasoning for
`"None reported"` in `PRECIPITATION` and `"No effect"` in `EFFECT`.

## Deploying

**Streamlit Community Cloud** is the path of least resistance and connects
straight to GitHub:

1. Push this repo to GitHub (public).
2. Go to https://share.streamlit.io, sign in with GitHub, pick the repo, set the
   main file to `app.py`, deploy.
3. Free, and it redeploys on every push.

The one catch: `data/raw/` is gitignored, so the host has no data. Either
commit `data/strikes.parquet` (it compresses well — the full export lands
around 20–40 MB, under GitHub's 100 MB hard limit) by un-commenting that line
in `.gitignore`, or add a download step. Committing the prepped file is simpler.

**GitHub Pages will not work for this.** Pages serves static files only, and
Streamlit needs a running Python process. If Pages specifically is a
requirement, the alternative is to precompute the aggregates to JSON and write
a static MapLibre or deck.gl front end — noticeably more work for less
interactivity. Other server-friendly options are Hugging Face Spaces (free,
Streamlit-native), Render, and Fly.io.

## Performance

Filtering 56k records takes about 7 ms; airport aggregation about 0.3 s. At the
full ~300k records expect roughly 5× that, still well inside interactive range.
`@st.cache_data` means the file loads once per session. If it ever feels slow,
the fix is to cache `airport_summary` on the filter tuple rather than to reach
for a database.
