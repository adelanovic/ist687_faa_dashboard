"""
Pure data logic for the FAA strike dashboard.

No streamlit imports here on purpose: everything in this file is plain
pandas, so it can be unit-tested, profiled, or reused in a notebook.
"""

import os

import numpy as np
import pandas as pd

DATA_CANDIDATES = [
    os.path.join("data", "strikes.parquet"),
    os.path.join("data", "strikes.csv.gz"),
    os.path.join("data", "strikes.csv"),
]

DAMAGE_ORDER = ["No damage", "Minor", "Uncertain", "Substantial", "Destroyed"]

# Airports whose "location" is not a real fixed point.
NON_AIRPORT = {"Unknown", "Private Air Strip", "Remote_Water", "Oil Rig",
               "Unknown Airport"}

# The STATE column also carries territories and foreign codes. Plotly's
# "USA-states" mode silently drops unknown codes, but they would still stretch
# the colour scale, so filter explicitly.
US_STATES = set("""
AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO
MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC
""".split())


def load(path=None):
    """Read the prepped file produced by prep_data.py."""
    if path is None:
        for p in DATA_CANDIDATES:
            if os.path.exists(p):
                path = p
                break
    if path is None:
        raise FileNotFoundError(
            "No prepped data found. Run `python prep_data.py` first."
        )
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)

    df["INCIDENT_DATE"] = pd.to_datetime(df["INCIDENT_DATE"], errors="coerce")
    # Guard the CSV round-trip: any label pandas may have re-read as NaN.
    for col, fill in (("DAMAGE_LEVEL", "No damage"), ("PRECIPITATION", "None reported"),
                      ("EFFECT", "No effect"), ("SPECIES", "Unknown")):
        if col in df.columns:
            df[col] = df[col].astype("string").fillna(fill)
    df["DAMAGE_LEVEL"] = pd.Categorical(
        df["DAMAGE_LEVEL"], categories=DAMAGE_ORDER, ordered=True
    )
    for c in ("INCIDENT_YEAR", "INCIDENT_MONTH"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    if df["HAS_COORDS"].dtype == object:
        df["HAS_COORDS"] = df["HAS_COORDS"].astype(str).str.lower().eq("true")
    return df


def options(df):
    """Filter menu contents, derived from the data rather than hard-coded."""
    yrs = df["INCIDENT_YEAR"].dropna().astype(int)

    def uniq(col, drop=("Unrecorded",)):
        if col not in df.columns:
            return []
        vals = df[col].dropna().astype(str).unique().tolist()
        return sorted(v for v in vals if v not in drop)

    return {
        "year_min": int(yrs.min()),
        "year_max": int(yrs.max()),
        "states": sorted(df["STATE"].dropna().astype(str).unique().tolist()),
        "sizes": [s for s in ["Small", "Medium", "Large"] if s in uniq("SIZE", ())],
        "phases": uniq("PHASE_OF_FLIGHT", ()),
        "phase_groups": uniq("PHASE_GROUP", ()),
        "times": uniq("TIME_OF_DAY", ()),
        "damage_levels": DAMAGE_ORDER,
        "species": (
            df.loc[df["SPECIES_KNOWN"], "SPECIES"]
            .value_counts().index.tolist()
        ),
    }


def apply_filters(df, years=None, months=None, states=None, sizes=None,
                  phase_groups=None, phases=None, times=None, species=None,
                  damage_levels=None, damage_only=False, known_species_only=False):
    """Boolean-mask filter. Every argument is optional; None means 'no filter'."""
    m = pd.Series(True, index=df.index)

    if years:
        lo, hi = years
        m &= df["INCIDENT_YEAR"].between(lo, hi)
    if months:
        m &= df["INCIDENT_MONTH"].isin(months)
    if states:
        m &= df["STATE"].isin(states)
    if sizes:
        m &= df["SIZE"].isin(sizes)
    if phase_groups:
        m &= df["PHASE_GROUP"].isin(phase_groups)
    if phases:
        m &= df["PHASE_OF_FLIGHT"].isin(phases)
    if times:
        m &= df["TIME_OF_DAY"].isin(times)
    if species:
        m &= df["SPECIES"].isin(species)
    if damage_levels:
        m &= df["DAMAGE_LEVEL"].astype(str).isin(damage_levels)
    if damage_only:
        m &= df["INDICATED_DAMAGE"].eq(1)
    if known_species_only:
        m &= df["SPECIES_KNOWN"]

    return df.loc[m]


def _top(s):
    """Most common non-null value in a Series, or '—'."""
    s = s.dropna()
    return s.mode().iloc[0] if len(s) else "—"


def airport_summary(df, min_strikes=1):
    """One row per mappable airport. This is what the map draws."""
    d = df[df["HAS_COORDS"] & ~df["AIRPORT"].isin(NON_AIRPORT)]
    if d.empty:
        return pd.DataFrame(
            columns=["AIRPORT_ID", "AIRPORT", "STATE", "LATITUDE", "LONGITUDE",
                     "strikes", "damaging", "damage_rate", "cost_total",
                     "cost_per_strike", "pct_large", "pct_night",
                     "top_species", "top_phase", "first_year", "last_year"]
        )

    g = d.groupby(["AIRPORT_ID", "AIRPORT", "STATE"], observed=True, dropna=False)
    out = g.agg(
        LATITUDE=("LATITUDE", "median"),
        LONGITUDE=("LONGITUDE", "median"),
        strikes=("INDICATED_DAMAGE", "size"),
        damaging=("INDICATED_DAMAGE", "sum"),
        cost_total=("COST_TOTAL", "sum"),
        first_year=("INCIDENT_YEAR", "min"),
        last_year=("INCIDENT_YEAR", "max"),
        top_species=("SPECIES", _top),
        top_phase=("PHASE_OF_FLIGHT", _top),
    ).reset_index()

    large = d["SIZE"].eq("Large").groupby(
        [d["AIRPORT_ID"], d["AIRPORT"], d["STATE"]], observed=True, dropna=False
    ).mean().reset_index(name="pct_large")
    night = d["TIME_OF_DAY"].eq("Night").groupby(
        [d["AIRPORT_ID"], d["AIRPORT"], d["STATE"]], observed=True, dropna=False
    ).mean().reset_index(name="pct_night")

    out = out.merge(large, on=["AIRPORT_ID", "AIRPORT", "STATE"], how="left")
    out = out.merge(night, on=["AIRPORT_ID", "AIRPORT", "STATE"], how="left")

    out["damage_rate"] = out["damaging"] / out["strikes"]
    out["pct_large"] = out["pct_large"].fillna(0) * 100
    out["pct_night"] = out["pct_night"].fillna(0) * 100
    out["cost_per_strike"] = out["cost_total"] / out["strikes"]

    out = out[out["strikes"] >= min_strikes]
    return out.sort_values("strikes", ascending=False).reset_index(drop=True)


def state_summary(df):
    """One row per state, for the choropleth."""
    d = df[df["STATE"].isin(US_STATES)]
    if d.empty:
        return pd.DataFrame(columns=["STATE", "strikes", "damaging",
                                     "damage_rate", "cost_total"])
    out = d.groupby("STATE", observed=True).agg(
        strikes=("INDICATED_DAMAGE", "size"),
        damaging=("INDICATED_DAMAGE", "sum"),
        cost_total=("COST_TOTAL", "sum"),
    ).reset_index()
    out["damage_rate"] = out["damaging"] / out["strikes"]
    return out.sort_values("strikes", ascending=False)


def species_summary(df, min_strikes=20):
    """Frequency vs. severity per species — the actionable quadrant chart."""
    d = df[df["SPECIES_KNOWN"]]
    if d.empty:
        return pd.DataFrame(columns=["SPECIES", "strikes", "damaging",
                                     "damage_rate", "size_class",
                                     "cost_total", "mean_cost_per_strike"])
    out = d.groupby("SPECIES", observed=True).agg(
        strikes=("INDICATED_DAMAGE", "size"),
        damaging=("INDICATED_DAMAGE", "sum"),
        cost_total=("COST_TOTAL", "sum"),
        size_class=("SIZE", _top),
    ).reset_index()
    out["damage_rate"] = out["damaging"] / out["strikes"]
    out["mean_cost_per_strike"] = out["cost_total"] / out["strikes"]
    out = out[out["strikes"] >= min_strikes]
    return out.sort_values("strikes", ascending=False).reset_index(drop=True)


def monthly_series(df):
    """Strike counts by calendar month, for the trend view."""
    d = df.dropna(subset=["INCIDENT_DATE"])
    if d.empty:
        return pd.DataFrame(columns=["month", "strikes", "damaging"])
    out = (
        d.set_index("INCIDENT_DATE")
        .resample("MS")
        .agg(strikes=("INDICATED_DAMAGE", "size"),
             damaging=("INDICATED_DAMAGE", "sum"))
        .reset_index()
        .rename(columns={"INCIDENT_DATE": "month"})
    )
    return out


def seasonality(df):
    """Mean strikes per calendar month, averaged over years present."""
    d = df.dropna(subset=["INCIDENT_MONTH", "INCIDENT_YEAR"])
    if d.empty:
        return pd.DataFrame(columns=["INCIDENT_MONTH", "strikes"])
    per = d.groupby(["INCIDENT_YEAR", "INCIDENT_MONTH"], observed=True).size()
    out = per.groupby("INCIDENT_MONTH").mean().reset_index(name="strikes")
    return out.sort_values("INCIDENT_MONTH")


def kpis(df):
    """Headline numbers for the top of the page."""
    n = len(df)
    dmg = int(df["INDICATED_DAMAGE"].sum()) if n else 0
    return {
        "strikes": n,
        "damaging": dmg,
        "damage_rate": (dmg / n) if n else 0.0,
        "airports": int(df.loc[df["HAS_COORDS"], "AIRPORT_ID"].nunique()) if n else 0,
        "cost_total": float(df["COST_TOTAL"].sum()) if n else 0.0,
        "species": int(df.loc[df["SPECIES_KNOWN"], "SPECIES"].nunique()) if n else 0,
        "coord_coverage": float(df["HAS_COORDS"].mean()) if n else 0.0,
    }


def color_ramp(values, lo=None, hi=None, reverse=False):
    """
    Map values to RGB triples for pydeck. Viridis-like, computed inline so the
    app has no matplotlib dependency.
    """
    v = pd.to_numeric(pd.Series(values), errors="coerce").fillna(0).to_numpy(float)
    lo = np.nanmin(v) if lo is None else lo
    hi = np.nanmax(v) if hi is None else hi
    if hi <= lo:
        t = np.zeros_like(v)
    else:
        t = np.clip((v - lo) / (hi - lo), 0, 1)
    if reverse:
        t = 1 - t
    stops = np.array([
        [68, 1, 84], [59, 82, 139], [33, 145, 140],
        [94, 201, 98], [253, 231, 37],
    ], dtype=float)
    idx = t * (len(stops) - 1)
    lo_i = np.floor(idx).astype(int)
    hi_i = np.clip(lo_i + 1, 0, len(stops) - 1)
    frac = (idx - lo_i)[:, None]
    rgb = stops[lo_i] * (1 - frac) + stops[hi_i] * frac
    return [[int(r), int(g), int(b)] for r, g, b in rgb]
