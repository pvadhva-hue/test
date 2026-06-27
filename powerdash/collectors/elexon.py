"""Elexon BMRS Insights Solution API client.

Docs: https://developer.data.elexon.co.uk/api-details
Most endpoints are public and unauthenticated.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from ._http import get_json


BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1"


def _to_date(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _records_to_df(payload: Any, key: str = "data") -> pd.DataFrame:
    if payload is None:
        return pd.DataFrame()
    if isinstance(payload, dict):
        records = payload.get(key) or payload.get("results") or payload.get("items") or []
    else:
        records = payload
    df = pd.DataFrame(records)
    for col in df.columns:
        if "time" in col.lower() or col.lower().endswith("date"):
            try:
                df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
            except (TypeError, ValueError):
                pass
    return df


class ElexonClient:
    """Thin wrapper around the BMRS Insights Solution REST API."""

    def __init__(self, base_url: str = BASE_URL, *, ttl_seconds: int = 1800):
        self.base_url = base_url.rstrip("/")
        self.ttl_seconds = ttl_seconds

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        return get_json(url, params=params, ttl_seconds=self.ttl_seconds)

    # --- System & imbalance prices ---------------------------------------

    def system_prices(self, settlement_date: str | date) -> pd.DataFrame:
        """System Buy / Sell prices by settlement period for a given date."""
        path = f"/balancing/settlement/system-prices/{_to_date(settlement_date)}"
        return _records_to_df(self._get(path))

    def market_index(self, from_dt: datetime, to_dt: datetime) -> pd.DataFrame:
        """N2EX / APX intraday market index reference prices."""
        params = {
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "format": "json",
        }
        return _records_to_df(self._get("/balancing/pricing/market-index", params))

    # --- Demand ----------------------------------------------------------

    def demand_outturn(self, settlement_date: str | date) -> pd.DataFrame:
        path = f"/demand/actual/total/{_to_date(settlement_date)}"
        return _records_to_df(self._get(path))

    def demand_day_ahead(self, settlement_date: str | date) -> pd.DataFrame:
        path = f"/forecast/demand/day-ahead/{_to_date(settlement_date)}"
        return _records_to_df(self._get(path))

    # --- Generation ------------------------------------------------------

    def generation_by_fuel(self, from_dt: datetime, to_dt: datetime) -> pd.DataFrame:
        """Actual MW per fuel type at 30 min resolution."""
        params = {
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "format": "json",
        }
        return _records_to_df(self._get("/generation/actual/per-type", params))

    def wind_solar_forecast(self, from_dt: datetime, to_dt: datetime) -> pd.DataFrame:
        """Day-ahead wind and solar generation forecast (MW)."""
        params = {
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "format": "json",
        }
        return _records_to_df(
            self._get("/forecast/generation/wind-and-solar/day-ahead", params)
        )

    # --- Interconnectors -------------------------------------------------

    def interconnector_flows(self, from_dt: datetime, to_dt: datetime) -> pd.DataFrame:
        params = {
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "format": "json",
        }
        return _records_to_df(self._get("/generation/outturn/interconnectors", params))

    # --- Balancing mechanism --------------------------------------------

    def bid_offer_acceptances(self, settlement_date: str | date) -> pd.DataFrame:
        path = f"/balancing/acceptances/all/{_to_date(settlement_date)}"
        return _records_to_df(self._get(path))


def settlement_period_to_time(date_str: str, settlement_period: int) -> datetime:
    """Map UK settlement period (1..48 normally) to a UTC datetime."""
    base = datetime.fromisoformat(date_str)
    return base + timedelta(minutes=30 * (settlement_period - 1))
