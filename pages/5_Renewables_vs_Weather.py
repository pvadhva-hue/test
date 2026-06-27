"""Renewables forecast vs outturn alongside weather forecast vs reanalysis."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from powerdash.dashboard import data
from powerdash.dashboard.utils import empty_warning


st.title("Renewables vs Weather")
st.caption(
    "Compare day-ahead wind/solar forecasts with actual generation, and "
    "day-ahead weather forecasts with ERA5 outturn weather. "
    "Forecast errors in MW often track forecast errors in wind speed."
)

end = st.date_input("End date", value=date.today() - timedelta(days=1))
start = end - timedelta(days=3)

st.subheader("Forecast vs actual generation")
fc = data.wind_solar_forecast(start, end)
gen = data.generation_by_fuel(start, end)
if empty_warning(fc, "Elexon wind/solar forecast") or empty_warning(gen, "Elexon generation"):
    st.stop()

fc = fc.copy()
gen = gen.copy()
t_fc = "startTime" if "startTime" in fc.columns else fc.columns[0]
t_gen = "startTime" if "startTime" in gen.columns else gen.columns[0]

fuel_col = next((c for c in ("psrType", "fuelType", "fuel") if c in gen.columns), None)
val_gen = next((c for c in ("quantity", "generation", "value") if c in gen.columns), None)
val_fc = next((c for c in ("generation", "quantity", "value") if c in fc.columns), None)
fc_type = next((c for c in ("psrType", "businessType", "type") if c in fc.columns), None)

if not all([fuel_col, val_gen, val_fc, fc_type]):
    st.warning("Forecast/actual schemas didn't expose expected columns; showing tables.")
    st.dataframe(fc.head(40), use_container_width=True, hide_index=True)
    st.dataframe(gen.head(40), use_container_width=True, hide_index=True)
    st.stop()

wind_actual = (
    gen[gen[fuel_col].astype(str).str.lower().str.contains("wind")]
    .groupby(t_gen)[val_gen].sum().rename("actual").to_frame()
)
wind_forecast = (
    fc[fc[fc_type].astype(str).str.lower().str.contains("wind")]
    .groupby(t_fc)[val_fc].sum().rename("forecast").to_frame()
)
solar_actual = (
    gen[gen[fuel_col].astype(str).str.lower().str.contains("solar|pv")]
    .groupby(t_gen)[val_gen].sum().rename("actual").to_frame()
)
solar_forecast = (
    fc[fc[fc_type].astype(str).str.lower().str.contains("solar|pv")]
    .groupby(t_fc)[val_fc].sum().rename("forecast").to_frame()
)

for label, actual, forecast in [
    ("Wind", wind_actual, wind_forecast),
    ("Solar", solar_actual, solar_forecast),
]:
    if actual.empty and forecast.empty:
        continue
    merged = actual.join(forecast, how="outer").sort_index().reset_index().rename(
        columns={actual.index.name or "index": "time"}
    )
    if "time" not in merged.columns:
        merged = merged.rename(columns={merged.columns[0]: "time"})
    long = merged.melt("time", value_vars=[c for c in ("actual", "forecast")
                                            if c in merged.columns],
                      var_name="series", value_name="MW")
    fig = px.line(long, x="time", y="MW", color="series", title=f"{label}: forecast vs actual",
                  labels={"time": "Time (UTC)"})
    st.plotly_chart(fig, use_container_width=True)

    if {"actual", "forecast"}.issubset(merged.columns):
        err = (merged["actual"] - merged["forecast"]).dropna()
        if len(err):
            st.write(f"{label} forecast error — mean: **{err.mean():.0f} MW**, "
                     f"std: **{err.std():.0f} MW**, MAE: **{err.abs().mean():.0f} MW**")

st.subheader("Weather: forecast vs ERA5 outturn")
wf = data.weather_forecast()
wo = data.weather_outturn(start, end)
if not wf.empty and not wo.empty:
    wf = wf[wf["time"].dt.date >= start - timedelta(days=1)]
    fig = px.line(wf, x="time", y="wind_speed_100m", color="site",
                  title="48h wind speed forecast (100m, m/s)")
    st.plotly_chart(fig, use_container_width=True)
    fig2 = px.line(wo, x="time", y="wind_speed_100m", color="site",
                   title="ERA5 outturn wind speed (100m, m/s)")
    st.plotly_chart(fig2, use_container_width=True)

    merged = wf.merge(wo, on=["time", "site"], suffixes=("_fc", "_out"))
    if not merged.empty:
        merged["wind_err"] = merged["wind_speed_100m_out"] - merged["wind_speed_100m_fc"]
        fig3 = px.box(merged, x="site", y="wind_err",
                       title="Wind speed forecast error by site (m/s)",
                       labels={"wind_err": "outturn - forecast"})
        st.plotly_chart(fig3, use_container_width=True)
else:
    st.info("Weather data unavailable — Open-Meteo archive has a ~5 day lag.")
