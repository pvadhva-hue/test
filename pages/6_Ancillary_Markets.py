"""Frequency response / reserve markets (Dynamic Containment etc)."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from powerdash.dashboard import data
from powerdash.dashboard.utils import empty_warning, first_numeric_col, first_time_col


st.title("Ancillary Markets")
st.caption("Frequency response auction results published by NESO.")

product = st.selectbox(
    "Product",
    ["dc", "dm", "dr", "stor"],
    format_func=lambda p: {
        "dc": "Dynamic Containment (DC, low + high)",
        "dm": "Dynamic Moderation (DM)",
        "dr": "Dynamic Regulation (DR)",
        "stor": "Short Term Operating Reserve",
    }[p],
)

df = data.ancillary(product=product)
if empty_warning(df, f"NESO ancillary results ({product.upper()})"):
    st.stop()

t_col = first_time_col(df)
price_col = first_numeric_col(df, prefer=["clearing_price", "clearingPrice",
                                            "price", "accepted_price"])
volume_col = first_numeric_col(df, prefer=["accepted_volume", "acceptedVolume",
                                             "volume", "mw"])

c1, c2 = st.columns(2)
if t_col and price_col:
    fig = px.line(df.sort_values(t_col), x=t_col, y=price_col,
                  labels={price_col: "£/MW/h", t_col: "Settlement"})
    c1.plotly_chart(fig, use_container_width=True)
if t_col and volume_col:
    fig2 = px.bar(df.sort_values(t_col), x=t_col, y=volume_col,
                  labels={volume_col: "MW", t_col: "Settlement"})
    c2.plotly_chart(fig2, use_container_width=True)

st.dataframe(df.head(80), use_container_width=True, hide_index=True)
st.caption(
    "Dataset schema can change when NESO publishes new resource versions. "
    "Override `RESOURCES` in `powerdash/collectors/neso.py` to repoint."
)
