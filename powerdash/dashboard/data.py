"""Streamlit-cached data loaders used by all dashboard pages."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from ..collectors import ElexonClient, NesoClient, WeatherClient

_TTL = 30 * 60  # 30 minutes


@st.cache_resource
def elexon() -> ElexonClient:
    return ElexonClient()


@st.cache_resource
def neso() -> NesoClient:
    return NesoClient()


@st.cache_resource
def weather() -> WeatherClient:
    return WeatherClient()


def _to_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


@st.cache_data(ttl=_TTL, show_spinner=False)
def system_prices(target_date: date) -> pd.DataFrame:
    try:
        df = elexon().system_prices(target_date)
    except Exception as exc:  # noqa: BLE001
        st.warning(f"System prices fetch failed: {exc}")
        return pd.DataFrame()
    if df.empty:
        return df
    df = df.copy()
    if "startTime" in df.columns:
        df["startTime"] = pd.to_datetime(df["startTime"], utc=True)
    return df


@st.cache_data(ttl=_TTL, show_spinner=False)
def demand_outturn(target_date: date) -> pd.DataFrame:
    try:
        return elexon().demand_outturn(target_date)
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Demand fetch failed: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=_TTL, show_spinner=False)
def generation_by_fuel(start: date, end: date) -> pd.DataFrame:
    try:
        return elexon().generation_by_fuel(_to_utc(start), _to_utc(end) + timedelta(days=1))
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Generation fetch failed: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=_TTL, show_spinner=False)
def wind_solar_forecast(start: date, end: date) -> pd.DataFrame:
    try:
        return elexon().wind_solar_forecast(_to_utc(start), _to_utc(end) + timedelta(days=1))
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Wind/solar forecast fetch failed: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=_TTL, show_spinner=False)
def interconnectors(start: date, end: date) -> pd.DataFrame:
    try:
        return elexon().interconnector_flows(_to_utc(start), _to_utc(end) + timedelta(days=1))
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Interconnector fetch failed: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=_TTL, show_spinner=False)
def market_index(start: date, end: date) -> pd.DataFrame:
    try:
        return elexon().market_index(_to_utc(start), _to_utc(end) + timedelta(days=1))
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Market index fetch failed: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=_TTL, show_spinner=False)
def da_prices(limit: int = 2000) -> pd.DataFrame:
    try:
        return neso().da_prices(limit=limit)
    except Exception as exc:  # noqa: BLE001
        st.warning(f"NESO DA prices fetch failed: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=_TTL, show_spinner=False)
def ancillary(product: str = "dc") -> pd.DataFrame:
    try:
        return neso().ancillary_results(product=product)
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Ancillary fetch failed: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=_TTL, show_spinner=False)
def weather_forecast() -> pd.DataFrame:
    try:
        return weather().forecast()
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Weather forecast fetch failed: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=_TTL, show_spinner=False)
def weather_outturn(start: date, end: date) -> pd.DataFrame:
    try:
        return weather().outturn(start, end)
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Weather outturn fetch failed: {exc}")
        return pd.DataFrame()
