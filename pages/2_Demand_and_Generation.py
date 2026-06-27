"""Demand outturn and generation by fuel type."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from powerdash.dashboard import data
from powerdash.dashboard.utils import default_date_range, empty_warning, utc_today


st.title("Demand & Generation")
st.caption("Demand outturn and the generation fuel mix.")

c1, c2 = st.columns(2)
target = c1.date_input("Demand date", value=utc_today())
start, end = c2.date_input("Generation window", value=default_date_range(3))

st.subheader("Demand outturn (Elexon)")
dem = data.demand_outturn(target)
if not empty_warning(dem, "Elexon /demand/actual/total"):
    t = "startTime" if "startTime" in dem.columns else dem.columns[0]
    val_cols = [c for c in ("demand", "transmissionSystemDemand",
                            "englandWalesDemand") if c in dem.columns]
    if val_cols:
        long = dem.melt(t, value_vars=val_cols, var_name="series",
                        value_name="MW")
        fig = px.line(long, x=t, y="MW", color="series",
                      labels={t: "Time (UTC)"})
        st.plotly_chart(fig, use_container_width=True)
    st.dataframe(dem.head(60), use_container_width=True, hide_index=True)

st.subheader("Generation by fuel type (Elexon)")
gen = data.generation_by_fuel(start, end)
if not empty_warning(gen, "Elexon /generation/actual/per-type"):
    t_col = "startTime" if "startTime" in gen.columns else None
    fuel_col = None
    val_col = None
    for c in ("psrType", "fuelType", "fuel"):
        if c in gen.columns:
            fuel_col = c
            break
    for c in ("quantity", "generation", "value"):
        if c in gen.columns:
            val_col = c
            break
    if t_col and fuel_col and val_col:
        fig = px.area(
            gen.sort_values(t_col),
            x=t_col,
            y=val_col,
            color=fuel_col,
            labels={val_col: "MW", t_col: "Time (UTC)"},
        )
        st.plotly_chart(fig, use_container_width=True)

        latest_t = gen[t_col].max()
        latest = gen[gen[t_col] == latest_t].groupby(fuel_col)[val_col].sum().sort_values(ascending=False)
        st.write("**Latest fuel mix (MW):**")
        st.bar_chart(latest)
    st.dataframe(gen.head(80), use_container_width=True, hide_index=True)
