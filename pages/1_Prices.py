"""System & day-ahead prices."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from powerdash.dashboard import data
from powerdash.dashboard.utils import (
    default_date_range,
    empty_warning,
    first_numeric_col,
    first_time_col,
    utc_today,
)


st.title("Prices")
st.caption("System imbalance prices, day-ahead reference, intraday market index.")

c1, c2 = st.columns(2)
target = c1.date_input("System price date", value=utc_today())
start, end = c2.date_input("Market index window", value=default_date_range(7))

st.subheader("System buy / sell prices (Elexon)")
sp = data.system_prices(target)
if not empty_warning(sp, "Elexon /balancing/settlement/system-prices"):
    cols = [c for c in ("systemSellPrice", "systemBuyPrice", "netImbalanceVolume")
            if c in sp.columns]
    if cols and "startTime" in sp.columns:
        long = sp.melt("startTime", value_vars=cols, var_name="series",
                      value_name="value")
        fig = px.line(long, x="startTime", y="value", color="series",
                      markers=True, labels={"value": "£/MWh or MWh", "startTime": "Time (UTC)"})
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(sp[["startTime", *cols]].tail(48), use_container_width=True,
                     hide_index=True)
    else:
        st.dataframe(sp, use_container_width=True, hide_index=True)

st.subheader("N2EX / APX market index (Elexon)")
mi = data.market_index(start, end)
if not empty_warning(mi, "Elexon /balancing/pricing/market-index"):
    t = first_time_col(mi)
    p = first_numeric_col(mi, prefer=["price"])
    name_col = "dataProvider" if "dataProvider" in mi.columns else None
    if t and p:
        fig = px.line(mi.sort_values(t), x=t, y=p,
                      color=name_col, markers=False,
                      labels={p: "£/MWh", t: "Time (UTC)"})
        st.plotly_chart(fig, use_container_width=True)
    st.dataframe(mi.tail(50), use_container_width=True, hide_index=True)

st.subheader("Day-Ahead hourly prices (NESO portal)")
da = data.da_prices(limit=2000)
if not empty_warning(da, "NESO datastore (DA prices)"):
    t = first_time_col(da)
    p = first_numeric_col(da, prefer=["price", "da_price", "n2ex_price",
                                       "epex_price", "day_ahead_price"])
    if t and p:
        view = da[[t, p]].dropna().sort_values(t).tail(24 * 14)
        fig = px.line(view, x=t, y=p, labels={p: "£/MWh", t: "Time"})
        st.plotly_chart(fig, use_container_width=True)
        st.metric("Latest", f"£{view[p].iloc[-1]:.2f}/MWh")
        st.metric("14-day mean", f"£{view[p].mean():.2f}/MWh")
        st.metric("14-day max spread (within day)",
                  f"£{view.assign(d=view[t].dt.date).groupby('d')[p].agg(lambda s: s.max() - s.min()).mean():.2f}/MWh")
    st.dataframe(da.tail(50), use_container_width=True, hide_index=True)
