"""
FAA Wildlife Strike Dashboard — data prep.

Reads every FAA export part from data/raw/ (xlsx or csv), cleans them,
and writes a single compact file the dashboard loads instantly.

Usage:
    python prep_data.py

Input:  data/raw/*.xlsx  (and/or *.csv)
Output: data/strikes.parquet   (falls back to strikes.csv.gz if pyarrow missing)
"""

import glob
import os
import sys

import numpy as np
import pandas as pd

RAW_DIR = os.path.join("data", "raw")
OUT_DIR = "data"

# Columns we keep. Everything else in the 102-column export is dropped:
# either fully redacted, near-100% missing, or per-part administrative noise.
KEEP = [
    "INDEX_NR", "INCIDENT_DATE", "INCIDENT_MONTH", "INCIDENT_YEAR",
    "TIME_OF_DAY", "AIRPORT_ID", "AIRPORT", "LATITUDE", "LONGITUDE",
    "STATE", "FAAREGION", "OPERATOR", "AIRCRAFT", "AC_CLASS", "AC_MASS",
    "TYPE_ENG", "NUM_ENGS", "PHASE_OF_FLIGHT", "HEIGHT", "SPEED",
    "DISTANCE", "SKY", "PRECIPITATION", "AOS",
    "COST_REPAIRS_INFL_ADJ", "COST_OTHER_INFL_ADJ",
    "INDICATED_DAMAGE", "DAMAGE_LEVEL", "EFFECT",
    "SPECIES", "SIZE", "NUM_SEEN", "NUM_STRUCK", "WARNED",
    "NR_INJURIES", "NR_FATALITIES", "REMARKS",
]

# "No damage" rather than "None": pandas read_csv parses the literal string
# "None" as NaN, which silently deletes ~88% of the rows from any severity
# breakdown after a CSV round-trip. Same reason for the other two below.
DAMAGE_ORDER = ["No damage", "Minor", "Uncertain", "Substantial", "Destroyed"]
DAMAGE_MAP = {"N": "No damage", "M": "Minor", "M?": "Uncertain",
              "S": "Substantial", "D": "Destroyed"}

PHASE_GROUP = {
    "Parked": "Ground", "Taxi": "Ground",
    "Take-off Run": "Ground", "Landing Roll": "Ground",
    "Departure": "Terminal", "Climb": "Terminal",
    "Approach": "Terminal", "Arrival": "Terminal", "Local": "Terminal",
    "Descent": "Airborne", "En Route": "Airborne",
}


def find_files():
    files = sorted(glob.glob(os.path.join(RAW_DIR, "*.xlsx")))
    files += sorted(glob.glob(os.path.join(RAW_DIR, "*.csv")))
    if not files:
        sys.exit(
            f"No .xlsx or .csv files found in {RAW_DIR}/\n"
            "Download the parts from https://wildlife.faa.gov/search "
            "and drop them in that folder."
        )
    return files


def read_one(path):
    print(f"  reading {os.path.basename(path)} ...", end=" ", flush=True)
    if path.endswith(".xlsx"):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path, low_memory=False)
    print(f"{len(df):,} rows")
    return df


def load_raw():
    frames = [read_one(p) for p in find_files()]
    df = pd.concat(frames, ignore_index=True)

    # The FAA splits its export by record index; parts can overlap.
    before = len(df)
    if "INDEX_NR" in df.columns:
        df = df.drop_duplicates(subset="INDEX_NR", keep="first")
        if before != len(df):
            print(f"  dropped {before - len(df):,} duplicate INDEX_NR rows")

    cols = [c for c in KEEP if c in df.columns]
    missing = set(KEEP) - set(cols)
    if missing:
        print(f"  note: columns absent from this export: {sorted(missing)}")
    return df[cols].copy()


def clean(df):
    # --- dates -------------------------------------------------------
    df["INCIDENT_DATE"] = pd.to_datetime(df["INCIDENT_DATE"], errors="coerce")
    for c in ("INCIDENT_YEAR", "INCIDENT_MONTH"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")

    # --- damage ------------------------------------------------------
    # DAMAGE_LEVEL is blank whenever INDICATED_DAMAGE == 0: the reporter
    # skipped the severity box because there was nothing to describe.
    # Structurally missing, not missing-at-random. Fill, do not impute.
    df["INDICATED_DAMAGE"] = (
        pd.to_numeric(df["INDICATED_DAMAGE"], errors="coerce").fillna(0).astype(int)
    )
    lvl = df["DAMAGE_LEVEL"].astype("string").str.strip()
    lvl = lvl.where(df["INDICATED_DAMAGE"] == 1, "N")
    df["DAMAGE_LEVEL"] = (
        lvl.map(DAMAGE_MAP).fillna("No damage").astype(
            pd.CategoricalDtype(DAMAGE_ORDER, ordered=True)
        )
    )

    # --- geography ---------------------------------------------------
    for c in ("LATITUDE", "LONGITUDE"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # A handful of records carry impossible coordinates.
    bad = (
        df["LATITUDE"].abs().gt(90)
        | df["LONGITUDE"].abs().gt(180)
        | (df["LATITUDE"].eq(0) & df["LONGITUDE"].eq(0))
    )
    df.loc[bad, ["LATITUDE", "LONGITUDE"]] = np.nan
    df["HAS_COORDS"] = df["LATITUDE"].notna() & df["LONGITUDE"].notna()
    df["AIRPORT"] = df["AIRPORT"].astype("string").str.strip().str.title()
    df["STATE"] = df["STATE"].astype("string").str.strip().str.upper()

    # --- categoricals: missing is informative, so label it -----------
    for c in ("TIME_OF_DAY", "PHASE_OF_FLIGHT", "SKY", "SIZE", "AC_CLASS"):
        if c in df.columns:
            df[c] = df[c].astype("string").str.strip().fillna("Unrecorded")

    df["PHASE_GROUP"] = df["PHASE_OF_FLIGHT"].map(PHASE_GROUP).fillna("Unrecorded")
    # Blank precipitation means none was reported, not unknown weather.
    df["PRECIPITATION"] = (
        df["PRECIPITATION"].astype("string").str.strip().fillna("None reported")
    )
    df["EFFECT"] = df["EFFECT"].astype("string").str.strip().fillna("No effect")

    # --- species -----------------------------------------------------
    df["SPECIES"] = df["SPECIES"].astype("string").str.strip().fillna("Unknown")
    df["SPECIES_KNOWN"] = ~df["SPECIES"].str.lower().str.startswith("unknown")

    # --- numerics ----------------------------------------------------
    for c in ("HEIGHT", "SPEED", "DISTANCE", "AOS", "NUM_ENGS", "AC_MASS",
              "NR_INJURIES", "NR_FATALITIES",
              "COST_REPAIRS_INFL_ADJ", "COST_OTHER_INFL_ADJ"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    cost = df.get("COST_REPAIRS_INFL_ADJ", 0)
    other = df.get("COST_OTHER_INFL_ADJ", 0)
    df["COST_TOTAL"] = (
        pd.to_numeric(cost, errors="coerce").fillna(0)
        + pd.to_numeric(other, errors="coerce").fillna(0)
    )

    df["HEIGHT_BAND"] = pd.cut(
        df["HEIGHT"],
        bins=[-1, 0, 500, 3500, 100000],
        labels=["Ground (0 ft)", "1–500 ft", "501–3,500 ft", ">3,500 ft"],
    ).astype("string").fillna("Unrecorded")

    df["REMARKS"] = df["REMARKS"].astype("string").fillna("")

    df = df.sort_values("INDEX_NR").reset_index(drop=True)
    return df


def report(df):
    print("\n--- cleaned dataset ---")
    print(f"rows            {len(df):,}")
    print(f"columns         {df.shape[1]}")
    yrs = df["INCIDENT_YEAR"].dropna()
    print(f"years           {int(yrs.min())}–{int(yrs.max())}")
    print(f"airports        {df['AIRPORT_ID'].nunique():,} "
          f"({df.loc[df.HAS_COORDS, 'AIRPORT_ID'].nunique():,} mappable)")
    print(f"with coords     {df.HAS_COORDS.mean() * 100:.1f}%")
    print(f"damage rate     {df.INDICATED_DAMAGE.mean() * 100:.2f}%")
    print(f"species labels  {df['SPECIES'].nunique():,}")

    counts = df["INCIDENT_YEAR"].value_counts().sort_index()
    tail = counts.tail(6)
    if len(counts) > 6 and tail.iloc[-1] < 0.25 * counts.max():
        print(
            "\n  WARNING: the last few years have far fewer records than the peak:\n"
            f"    {tail.to_dict()}\n"
            "  The FAA export is split by record index, so a partial download\n"
            "  looks like a collapse in strikes. Check you have every part\n"
            "  before drawing any trend conclusions."
        )


def write(df):
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        path = os.path.join(OUT_DIR, "strikes.parquet")
        df.to_parquet(path, index=False)
    except Exception:
        path = os.path.join(OUT_DIR, "strikes.csv.gz")
        df.to_csv(path, index=False, compression="gzip")
        print("\n  (pyarrow not installed — wrote gzipped CSV instead."
              " `pip install pyarrow` for faster loads.)")
    size = os.path.getsize(path) / 1e6
    print(f"\nwrote {path}  ({size:.1f} MB)")


if __name__ == "__main__":
    print("Loading FAA parts...")
    raw = load_raw()
    print(f"\ncombined: {len(raw):,} rows")
    clean_df = clean(raw)
    report(clean_df)
    write(clean_df)
