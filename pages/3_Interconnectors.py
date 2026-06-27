"""Interconnector flows."""
from __future__ import annotations

import plotly.express as px
import streamlit as st

from powerdash.dashboard import data
from powerdash.dashboard.utils import default_date_range, empty_warning


st.title("Interconnectors")
st.caption("Cross-border flows. Positive = import to GB, negative = export.")

start, end = st.date_input("Window", value=default_date_range(3))

flows = data.interconnectors(start, end)
if empty_warning(flows, "Elexon /generation/outturn/interconnectors"):
    st.stop()

t_col = "startTime" if "startTime" in flows.columns else flows.columns[0]
name_col = None
val_col = None
for c in ("interconnectorName", "interconnector", "name"):
    if c in flows.columns:
        name_col = c
        break
for c in ("generation", "flow", "quantity", "value"):
    if c in flows.columns:
        val_col = c
        break

if name_col and val_col:
    fig = px.line(
        flows.sort_values(t_col),
        x=t_col,
        y=val_col,
        color=name_col,
        labels={val_col: "MW", t_col: "Time (UTC)"},
    )
    st.plotly_chart(fig, use_container_width=True)

    net = flows.groupby(t_col)[val_col].sum().reset_index()
    fig2 = px.area(net, x=t_col, y=val_col,
                   labels={val_col: "Net MW", t_col: "Time (UTC)"},
                   title="Net interconnector flow")
    st.plotly_chart(fig2, use_container_width=True)

    by_link = flows.groupby(name_col)[val_col].mean().sort_values(ascending=False)
    st.write("**Average flow by link (MW):**")
    st.bar_chart(by_link)

st.dataframe(flows.head(80), use_container_width=True, hide_index=True)
