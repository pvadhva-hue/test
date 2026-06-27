"""BESS arbitrage margin analysis.

The page lets you size a battery and inspect the perfect-foresight daily
revenue it would have captured from:

- Day-ahead hourly prices (NESO portal)
- Imbalance system buy/sell prices (Elexon, half-hourly)

The DA result is the baseline. The imbalance uplift shows how much more
a perfect optimiser could have captured by trading half-hour spreads
within the day on top of the DA cycle.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from powerdash.dashboard import data
from powerdash.dashboard.utils import empty_warning, first_numeric_col, first_time_col
from powerdash.services.bess import BatteryConfig, daily_bess_summary


st.title("BESS Trading Margins")
st.caption("Perfect-foresight daily arbitrage revenue. Upper bound on what an optimiser could capture.")

with st.sidebar:
    st.header("Battery")
    power = st.number_input("Power (MW)", 1.0, 1000.0, 50.0, step=1.0)
    duration = st.number_input("Duration (hours)", 0.5, 12.0, 2.0, step=0.5)
    eta = st.slider("Round-trip efficiency", 0.5, 0.99, 0.86, step=0.01)
    cycles = st.slider("Cycles per day", 1, 3, 1)
    days = st.slider("History (days)", 7, 90, 30)

cfg = BatteryConfig(power_mw=power, duration_h=duration,
                    round_trip_efficiency=eta, cycles_per_day=cycles)

st.metric("Usable energy", f"{cfg.energy_mwh:.1f} MWh")

end = date.today()
start = end - timedelta(days=days)

da_raw = data.da_prices(limit=24 * (days + 5))
if empty_warning(da_raw, "NESO DA prices"):
    st.stop()

t_col = first_time_col(da_raw)
p_col = first_numeric_col(
    da_raw,
    prefer=["price", "da_price", "n2ex_price", "epex_price",
            "day_ahead_price", "Price"],
)
if not t_col or not p_col:
    st.error(
        "Could not identify time/price columns in the NESO DA prices dataset. "
        "Resource schema may have changed — adjust `powerdash.collectors.neso.RESOURCES`."
    )
    st.stop()

da = da_raw[[t_col, p_col]].rename(columns={t_col: "time", p_col: "price"}).copy()
da["time"] = pd.to_datetime(da["time"], utc=True, errors="coerce")
da = da.dropna(subset=["time", "price"])
da = da[(da["time"].dt.date >= start) & (da["time"].dt.date <= end)]

# Pull imbalance prices day by day for the same window
sip_frames: list[pd.DataFrame] = []
for d in pd.date_range(start, end, freq="D"):
    sp = data.system_prices(d.date())
    if not sp.empty:
        sip_frames.append(sp)
sip = pd.concat(sip_frames, ignore_index=True) if sip_frames else pd.DataFrame()

summary = daily_bess_summary(da, sip if not sip.empty else None, cfg)
if summary.empty:
    st.warning("Not enough price data in the selected window to compute margins.")
    st.stop()

st.subheader("Daily revenue")
melt_cols = [c for c in ("da_revenue_gbp", "sip_revenue_gbp") if c in summary.columns]
plot_df = summary.melt("date", value_vars=melt_cols, var_name="series",
                       value_name="GBP")
fig = px.bar(plot_df, x="date", y="GBP", color="series", barmode="group",
             labels={"GBP": "£ / day"})
st.plotly_chart(fig, use_container_width=True)

st.subheader("Capture spread (£/MWh)")
spread_cols = [c for c in ("da_spread_gbp_mwh", "sip_spread_gbp_mwh")
               if c in summary.columns]
if spread_cols:
    fig2 = px.line(summary, x="date", y=spread_cols, markers=True,
                   labels={"value": "£/MWh"})
    st.plotly_chart(fig2, use_container_width=True)

c1, c2, c3 = st.columns(3)
c1.metric("Total DA revenue", f"£{summary['da_revenue_gbp'].sum():,.0f}")
if "sip_revenue_gbp" in summary.columns:
    c2.metric("Total imbalance revenue", f"£{summary['sip_revenue_gbp'].sum():,.0f}")
    uplift = summary.get("sip_uplift_gbp", pd.Series(dtype=float)).sum()
    c3.metric("Imbalance uplift vs DA", f"£{uplift:,.0f}")

st.subheader("Detail")
st.dataframe(summary.sort_values("date", ascending=False),
             use_container_width=True, hide_index=True)

st.caption(
    "Method: choose the cheapest D hours to charge and the most expensive D hours "
    "to discharge each day (D = battery duration). Discharge must occur after "
    "charging within the day. Round-trip efficiency reduces delivered energy. "
    "This is an upper bound — real trading captures 40-70% of perfect foresight."
)
