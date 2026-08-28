"""
FAA Wildlife Strike Explorer — Streamlit dashboard.

Run:
    streamlit run app.py
"""

import numpy as np
import pandas as pd
import plotly.express as px
import pydeck as pdk
import streamlit as st

import core

st.set_page_config(
    page_title="FAA Wildlife Strike Explorer",
    page_icon="🛩",
    layout="wide",
    initial_sidebar_state="expanded",
)

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@st.cache_data(show_spinner="Loading strike records...")
def get_data():
    return core.load()


@st.cache_data
def get_options(_key):
    return core.options(get_data())


# Streamlit reruns this whole script on every widget change. At ~350k records
# the five aggregations below cost about a second combined, which is very
# noticeable on a checkbox click. Caching them on the filter values makes
# repeat states instant, and lets the map re-aggregate when only the
# min-strikes threshold moves without re-running the row filter.
#
# The cache key is the params tuple, never the DataFrame -- hashing 350k rows
# would cost more than the work it saves.

@st.cache_data(show_spinner=False)
def filtered(params):
    return core.apply_filters(get_data(), **dict(params))


@st.cache_data(show_spinner=False)
def agg_airports(params, min_strikes):
    return core.airport_summary(filtered(params), min_strikes=min_strikes)


@st.cache_data(show_spinner=False)
def agg_states(params):
    return core.state_summary(filtered(params))


@st.cache_data(show_spinner=False)
def agg_species(params, min_strikes):
    return core.species_summary(filtered(params), min_strikes=min_strikes)


@st.cache_data(show_spinner=False)
def agg_monthly(params):
    return core.monthly_series(filtered(params))


@st.cache_data(show_spinner=False)
def agg_seasonality(params):
    return core.seasonality(filtered(params))


@st.cache_data(show_spinner=False)
def agg_kpis(params):
    return core.kpis(filtered(params))


try:
    df = get_data()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

opts = get_options("v1")

# ----------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("Filters")

    years = st.slider(
        "Years", opts["year_min"], opts["year_max"],
        (opts["year_min"], opts["year_max"]),
    )

    month_names = st.multiselect("Months", MONTHS, default=[])
    months = [MONTHS.index(m) + 1 for m in month_names]

    states = st.multiselect("States", opts["states"], default=[])
    sizes = st.multiselect("Bird size", opts["sizes"], default=[])
    phase_groups = st.multiselect("Phase group", opts["phase_groups"], default=[])
    phases = st.multiselect("Phase of flight", opts["phases"], default=[])
    times = st.multiselect("Time of day", opts["times"], default=[])
    species = st.multiselect(
        "Species", opts["species"], default=[],
        help="Type to search. Only identified species are listed — roughly "
             "half of all records are logged as 'Unknown bird'.",
    )

    st.divider()
    damage_only = st.checkbox("Damaging strikes only", value=False)
    known_only = st.checkbox("Identified species only", value=False)
    min_strikes = st.number_input(
        "Min strikes per airport (map)", min_value=1, max_value=500, value=5,
        help="Airports below this threshold are hidden. Raise it to cut the "
             "noise of one-off reports; lower it to see everything.",
    )

    st.divider()
    st.caption(
        f"{len(df):,} records loaded · "
        f"{df['INCIDENT_YEAR'].min()}–{df['INCIDENT_YEAR'].max()}"
    )

# Tuples, not lists: cache keys must be hashable.
PARAMS = (
    ("years", tuple(years)),
    ("months", tuple(months)),
    ("states", tuple(states)),
    ("sizes", tuple(sizes)),
    ("phase_groups", tuple(phase_groups)),
    ("phases", tuple(phases)),
    ("times", tuple(times)),
    ("species", tuple(species)),
    ("damage_only", damage_only),
    ("known_species_only", known_only),
)

fdf = filtered(PARAMS)

# -------------------------------------------------------------------- head
st.title("FAA Wildlife Strike Explorer")

if fdf.empty:
    st.warning("No records match these filters. Widen them in the sidebar.")
    st.stop()

k = agg_kpis(PARAMS)
c = st.columns(6)
c[0].metric("Strikes", f"{k['strikes']:,}")
c[1].metric("Damaging", f"{k['damaging']:,}")
c[2].metric("Damage rate", f"{k['damage_rate'] * 100:.1f}%")
c[3].metric("Airports", f"{k['airports']:,}")
c[4].metric("Reported cost", f"${k['cost_total'] / 1e6:,.1f}M")
c[5].metric("Species", f"{k['species']:,}")

tab_map, tab_airport, tab_species, tab_trend, tab_records = st.tabs(
    ["Map", "Airport detail", "Species", "Trends", "Records"]
)

# --------------------------------------------------------------------- map
with tab_map:
    left, right = st.columns([3, 1])

    with right:
        metric = st.radio(
            "Colour points by",
            ["Strike volume", "Damage rate", "Cost per strike", "% large birds"],
            index=0,
        )
        basemap = st.selectbox(
            "Basemap", ["Light", "Dark", "Road"], index=0,
        )
        scale = st.slider("Point size", 0.5, 4.0, 1.5, 0.1)

    ap = agg_airports(PARAMS, int(min_strikes))

    if ap.empty:
        with left:
            st.info(
                "No airport meets the minimum-strikes threshold under these "
                "filters. Lower it in the sidebar."
            )
    else:
        field, fmt, reverse = {
            "Strike volume": ("strikes", "{:,.0f}", False),
            "Damage rate": ("damage_rate", "{:.1%}", False),
            "Cost per strike": ("cost_per_strike", "${:,.0f}", False),
            "% large birds": ("pct_large", "{:.1f}%", False),
        }[metric]

        # Strike volume is extremely long-tailed, so colour on a log scale;
        # rates and percentages are already bounded, so colour them linearly.
        vals = ap[field].astype(float)
        cvals = np.log1p(vals) if field in ("strikes", "cost_per_strike") else vals
        ap = ap.copy().assign(_color=core.color_ramp(cvals, reverse=reverse))

        # Radius from sqrt(count) so area, not radius, tracks volume.
        ap = ap.assign(
            _radius=np.sqrt(ap["strikes"]) * 900 * scale,
            _rate_str=(ap["damage_rate"] * 100).round(1).astype(str) + "%",
            _cost_str="$" + ap["cost_total"].round(0).map("{:,.0f}".format),
            _large_str=ap["pct_large"].round(1).astype(str) + "%",
            _night_str=ap["pct_night"].round(1).astype(str) + "%",
        )

        style = {
            "Light": "light", "Dark": "dark", "Road": "road",
        }[basemap]

        layer = pdk.Layer(
            "ScatterplotLayer",
            data=ap,
            get_position=["LONGITUDE", "LATITUDE"],
            get_fill_color="_color",
            get_radius="_radius",
            radius_min_pixels=3,
            radius_max_pixels=60,
            opacity=0.75,
            stroked=True,
            get_line_color=[255, 255, 255, 90],
            line_width_min_pixels=0.5,
            pickable=True,
            auto_highlight=True,
        )

        tooltip = {
            "html": (
                "<b>{AIRPORT}</b> ({AIRPORT_ID})<br/>"
                "{STATE} · {first_year}–{last_year}<br/><hr style='margin:4px 0'/>"
                "Strikes: <b>{strikes}</b><br/>"
                "Damaging: {damaging} ({_rate_str})<br/>"
                "Reported cost: {_cost_str}<br/>"
                "Large birds: {_large_str} · Night: {_night_str}<br/>"
                "Top species: {top_species}<br/>"
                "Top phase: {top_phase}"
            ),
            "style": {"backgroundColor": "#1e1e1e", "color": "white",
                      "fontSize": "12px"},
        }

        view = pdk.ViewState(latitude=39.5, longitude=-98.35, zoom=3.3, pitch=0)

        with left:
            st.pydeck_chart(
                pdk.Deck(layers=[layer], initial_view_state=view,
                         tooltip=tooltip, map_style=style),
                use_container_width=True,
            )

        with right:
            st.caption(
                f"{len(ap):,} airports shown. Point **area** is strike volume; "
                f"colour is {metric.lower()}."
            )
            if metric == "Strike volume":
                st.info(
                    "Volume tracks traffic. O'Hare leads because O'Hare is "
                    "busy, not because it is uniquely hazardous. Switch to "
                    "**Damage rate** — it conditions on a strike already "
                    "happening, so airport size cancels out.",
                    icon="⚠️",
                )

        st.divider()
        st.subheader("Strikes by state")
        ss = agg_states(PARAMS)
        choro_metric = st.radio(
            "Shade states by", ["Strikes", "Damage rate"],
            horizontal=True, key="choro",
        )
        col = "strikes" if choro_metric == "Strikes" else "damage_rate"
        fig = px.choropleth(
            ss, locations="STATE", locationmode="USA-states", color=col,
            scope="usa", color_continuous_scale="Viridis",
            hover_data={"strikes": ":,", "damaging": ":,",
                        "damage_rate": ":.1%", "STATE": True},
            labels={"strikes": "Strikes", "damage_rate": "Damage rate"},
        )
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=430)
        st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------- airport detail
with tab_airport:
    ap_all = agg_airports(PARAMS, 1)
    if ap_all.empty:
        st.info("No mappable airports under these filters.")
    else:
        labels = (
            ap_all["AIRPORT"].astype(str)
            + " (" + ap_all["AIRPORT_ID"].astype(str) + ") — "
            + ap_all["strikes"].astype(str) + " strikes"
        ).tolist()
        
        pick = st.selectbox("Airport", labels, index=0)
        row = ap_all.iloc[labels.index(pick)]
        sub = fdf[fdf["AIRPORT_ID"] == row["AIRPORT_ID"]]

        m = st.columns(5)
        m[0].metric("Strikes", f"{int(row['strikes']):,}")
        m[1].metric("Damage rate", f"{row['damage_rate'] * 100:.1f}%")
        m[2].metric("Reported cost", f"${row['cost_total'] / 1e6:,.2f}M")
        m[3].metric("Large birds", f"{row['pct_large']:.1f}%")
        m[4].metric("Night strikes", f"{row['pct_night']:.1f}%")

        a, b = st.columns(2)
        with a:
            st.markdown("**Top species**")
            top_sp = (
                sub[sub["SPECIES_KNOWN"]]["SPECIES"]
                .value_counts()
                .pipe(lambda x: x[x > 0])  # categorical value_counts keeps zeros
                .head(10).rename("strikes").reset_index()
            )
            if top_sp.empty:
                st.caption("No identified species at this airport.")
            else:
                st.plotly_chart(
                    px.bar(top_sp, x="strikes", y="SPECIES", orientation="h")
                    .update_layout(yaxis=dict(autorange="reversed"),
                                   margin=dict(l=0, r=0, t=10, b=0), height=320),
                    use_container_width=True,
                )
        with b:
            st.markdown("**Strikes by month**")
            bym = sub["INCIDENT_MONTH"].value_counts().sort_index()
            # reindex on a nullable Int64 index yields pd.NA, which plotly
            # cannot serialise — force a plain int dtype.
            bym = bym.reindex(range(1, 13), fill_value=0).astype(int)
            st.plotly_chart(
                px.bar(x=MONTHS, y=bym.values,
                       labels={"x": "", "y": "Strikes"})
                .update_layout(margin=dict(l=0, r=0, t=10, b=0), height=320),
                use_container_width=True,
            )

        st.markdown("**Phase of flight**")
        ph = sub["PHASE_OF_FLIGHT"].value_counts()
        ph = ph[ph > 0].rename("strikes").reset_index()
        st.dataframe(ph, use_container_width=True, hide_index=True)

        st.markdown("**Recent reports**")
        cols = ["INCIDENT_DATE", "SPECIES", "SIZE", "PHASE_OF_FLIGHT",
                "DAMAGE_LEVEL", "COST_TOTAL", "REMARKS"]
        st.dataframe(
            sub.sort_values("INCIDENT_DATE", ascending=False)[cols].head(200),
            use_container_width=True, hide_index=True,
        )

# ----------------------------------------------------------------- species
with tab_species:
    min_sp = st.slider("Minimum strikes to include a species", 5, 500, 25)
    sp = agg_species(PARAMS, min_sp)
    if sp.empty:
        st.info("No species clears that threshold under these filters.")
    else:
        st.markdown(
            "Frequency against severity. The **upper right** is the set that "
            "matters operationally: birds struck often *and* likely to cause "
            "damage when they are."
        )
        fig = px.scatter(
            sp, x="strikes", y="damage_rate", color="size_class",
            size="cost_total", hover_name="SPECIES", log_x=True,
            size_max=45,
            labels={"strikes": "Strikes (log scale)",
                    "damage_rate": "Damage rate", "size_class": "Size"},
            color_discrete_map={"Small": "#7fc97f", "Medium": "#fdc086",
                                "Large": "#e7298a"},
        )
        fig.update_layout(yaxis_tickformat=".0%", height=520,
                          margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)

        show = sp.copy()
        show["damage_rate"] = (show["damage_rate"] * 100).round(1)
        show["cost_total"] = show["cost_total"].round(0)
        show["mean_cost_per_strike"] = show["mean_cost_per_strike"].round(0)
        st.dataframe(
            show.rename(columns={
                "SPECIES": "Species", "strikes": "Strikes",
                "damaging": "Damaging", "damage_rate": "Damage rate %",
                "size_class": "Size", "cost_total": "Total cost $",
                "mean_cost_per_strike": "Mean cost/strike $",
            }),
            use_container_width=True, hide_index=True,
        )

# ------------------------------------------------------------------ trends
with tab_trend:
    ms = agg_monthly(PARAMS)
    st.markdown("**Monthly strike counts**")
    fig = px.line(ms, x="month", y=["strikes", "damaging"],
                  labels={"value": "Strikes", "month": "", "variable": ""})
    fig.update_layout(height=340, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, use_container_width=True)

    st.warning(
        "The long-run rise in reports is partly administrative. FAA wildlife "
        "strike reporting is voluntary and participation grew substantially "
        "over the covered period, so the trend mixes real change with "
        "reporting-rate change. The seasonal cycle below is the more "
        "defensible signal.",
        icon="📈",
    )

    a, b = st.columns(2)
    with a:
        st.markdown("**Seasonality** (mean strikes per calendar month)")
        se = agg_seasonality(PARAMS)
        se = se.copy()
        se["label"] = se["INCIDENT_MONTH"].astype(int).map(
            lambda i: MONTHS[i - 1]
        )
        st.plotly_chart(
            px.bar(se, x="label", y="strikes",
                   labels={"label": "", "strikes": "Mean strikes"})
            .update_layout(height=330, margin=dict(l=0, r=0, t=10, b=0)),
            use_container_width=True,
        )
    with b:
        st.markdown("**Damage rate by phase of flight**")
        ph = (
            fdf.groupby("PHASE_OF_FLIGHT", observed=True)
            .agg(strikes=("INDICATED_DAMAGE", "size"),
                 rate=("INDICATED_DAMAGE", "mean"))
            .reset_index()
        )
        ph = ph[ph["strikes"] >= 20].sort_values("rate", ascending=False)
        st.plotly_chart(
            px.bar(ph, x="rate", y="PHASE_OF_FLIGHT", orientation="h",
                   hover_data={"strikes": ":,"},
                   labels={"rate": "Damage rate", "PHASE_OF_FLIGHT": ""})
            .update_layout(xaxis_tickformat=".0%", height=330,
                           yaxis=dict(autorange="reversed"),
                           margin=dict(l=0, r=0, t=10, b=0)),
            use_container_width=True,
        )

    st.markdown("**Damage severity mix by bird size**")
    mix = (
        fdf.groupby(["SIZE", "DAMAGE_LEVEL"], observed=True)
        .size().rename("n").reset_index()
    )
    mix["share"] = mix["n"] / mix.groupby("SIZE")["n"].transform("sum")
    st.plotly_chart(
        px.bar(mix, x="SIZE", y="share", color="DAMAGE_LEVEL",
               category_orders={"SIZE": ["Small", "Medium", "Large"],
                                "DAMAGE_LEVEL": core.DAMAGE_ORDER},
               labels={"share": "Share of strikes", "SIZE": "",
                       "DAMAGE_LEVEL": "Damage"})
        .update_layout(yaxis_tickformat=".0%", height=360,
                       margin=dict(l=0, r=0, t=10, b=0)),
        use_container_width=True,
    )

# ----------------------------------------------------------------- records
with tab_records:
    st.markdown(f"**{len(fdf):,} records** match the current filters.")
    cols = ["INCIDENT_DATE", "AIRPORT", "STATE", "OPERATOR", "AIRCRAFT",
            "SPECIES", "SIZE", "PHASE_OF_FLIGHT", "HEIGHT", "TIME_OF_DAY",
            "DAMAGE_LEVEL", "COST_TOTAL", "REMARKS"]
    cols = [c for c in cols if c in fdf.columns]
    st.dataframe(
        fdf[cols].sort_values("INCIDENT_DATE", ascending=False).head(2000),
        use_container_width=True, hide_index=True,
    )
    st.caption("Showing the 2,000 most recent matches.")
    st.download_button(
        "Download filtered records (CSV)",
        fdf[cols].to_csv(index=False).encode("utf-8"),
        file_name="faa_strikes_filtered.csv",
        mime="text/csv",
    )
