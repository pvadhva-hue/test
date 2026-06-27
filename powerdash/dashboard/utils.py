"""Small helpers shared across dashboard pages."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd


def default_date_range(days: int = 7) -> tuple[date, date]:
    today = datetime.now(timezone.utc).date()
    return today - timedelta(days=days), today


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def empty_warning(df: pd.DataFrame, source: str) -> bool:
    import streamlit as st

    if df is None or df.empty:
        st.info(f"No data returned from {source} for the selected window.")
        return True
    return False


def first_time_col(df: pd.DataFrame) -> str | None:
    for c in ("startTime", "time", "settlementDate", "settlementTime",
              "publishTime", "halfHourEndTime"):
        if c in df.columns:
            return c
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            return c
    return None


def first_numeric_col(df: pd.DataFrame, prefer: list[str] | None = None) -> str | None:
    if prefer:
        for c in prefer:
            if c in df.columns and pd.api.types.is_numeric_dtype(df[c]):
                return c
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            return c
    return None
