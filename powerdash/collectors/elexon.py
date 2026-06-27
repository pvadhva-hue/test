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
        """Initial and Transmission System demand outturn (INDO / ITSDO)."""
        d = _to_date(settlement_date)
        params = {"settlementDateFrom": d, "settlementDateTo": d, "format": "json"}
        return _records_to_df(self._get("/demand/outturn", params))

    def demand_day_ahead(self, settlement_date: str | date) -> pd.DataFrame:
        d = _to_date(settlement_date)
        params = {"settlementDateFrom": d, "settlementDateTo": d, "format": "json"}
        return _records_to_df(self._get("/forecast/demand/day-ahead", params))

    # --- Generation ------------------------------------------------------

    def generation_by_fuel(self, from_dt: datetime, to_dt: datetime) -> pd.DataFrame:
        """Actual MW per fuel type at 30 min resolution (AGPT).

        The raw payload nests `{startTime, settlementPeriod, data: [{psrType,
        quantity, ...}]}` — this flattens into one row per (time, fuel).
        """
        params = {
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "format": "json",
        }
        payload = self._get("/generation/actual/per-type", params)
        if not isinstance(payload, dict):
            return pd.DataFrame()
        rows: list[dict] = []
        for snap in payload.get("data", []) or []:
            for inner in snap.get("data", []) or []:
                rows.append({
                    "startTime": snap.get("startTime"),
                    "settlementPeriod": snap.get("settlementPeriod"),
                    "psrType": inner.get("psrType"),
                    "businessType": inner.get("businessType"),
                    "quantity": inner.get("quantity"),
                })
        df = pd.DataFrame(rows)
        if not df.empty:
            df["startTime"] = pd.to_datetime(df["startTime"], utc=True, errors="coerce")
        return df

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

    # --- Generic dataset stream ----------------------------------------

    def dataset(self, code: str, from_dt: datetime, to_dt: datetime,
                **extra: Any) -> pd.DataFrame:
        """Generic accessor for /datasets/{code}/stream endpoints."""
        params: dict[str, Any] = {
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "format": "json",
            **extra,
        }
        payload = self._get(f"/datasets/{code.upper()}/stream", params)
        rows = payload if isinstance(payload, list) else (
            payload.get("data", []) if isinstance(payload, dict) else []
        )
        df = pd.DataFrame(rows)
        for col in df.columns:
            if "time" in col.lower() or col.lower().endswith("date"):
                df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
        return df

    # --- Wholesale prices (MID dataset) --------------------------------

    def market_index_data(self, from_dt: datetime, to_dt: datetime,
                           provider: str | None = None) -> pd.DataFrame:
        """Market Index Data (MID): hourly wholesale reference prices.

        Providers seen on this feed:
        - ``APXMIDP`` — APX/EPEX UK day-ahead hourly auction (GBP/MWh)
        - ``N2EXMIDP`` — Nord Pool N2EX day-ahead
        """
        df = self.dataset("MID", from_dt, to_dt)
        if provider and not df.empty and "dataProvider" in df.columns:
            df = df[df["dataProvider"] == provider].copy()
        return df

    def day_ahead_epex(self, from_dt: datetime, to_dt: datetime) -> pd.DataFrame:
        """Day-Ahead wholesale electricity price (GBP/MWh) from EPEX (APXMIDP)."""
        return self.market_index_data(from_dt, to_dt, provider="APXMIDP")

    def day_ahead_n2ex(self, from_dt: datetime, to_dt: datetime) -> pd.DataFrame:
        """Day-Ahead wholesale electricity price (GBP/MWh) from N2EX (N2EXMIDP)."""
        return self.market_index_data(from_dt, to_dt, provider="N2EXMIDP")

    # --- Balancing Mechanism -------------------------------------------

    def bid_offer_data(self, from_dt: datetime, to_dt: datetime) -> pd.DataFrame:
        """BOD: physical Bid / Offer prices and volumes per BMU per period."""
        return self.dataset("BOD", from_dt, to_dt)

    def bid_offer_acceptances(self, from_dt: datetime, to_dt: datetime) -> pd.DataFrame:
        """BOALF: Bid Offer Acceptances Level Flagged.

        Each row is a NESO instruction to a unit — `levelTo` is the MW
        target, `soFlag` marks system actions, `storFlag` marks STOR.
        """
        return self.dataset("BOALF", from_dt, to_dt)

    # --- BSAD (Balancing Services Adjustment Data) ----------------------

    def net_bsad(self, from_dt: datetime, to_dt: datetime) -> pd.DataFrame:
        """NETBSAD: net buy/sell price adjustments per settlement period."""
        return self.dataset("NETBSAD", from_dt, to_dt)

    def disaggregated_bsad(self, from_dt: datetime, to_dt: datetime) -> pd.DataFrame:
        """DISBSAD: disaggregated balancing services adjustment items.

        Includes STOR actions, system-flagged trades and other
        bilateral-balancing volumes that don't flow through the BM.
        """
        return self.dataset("DISBSAD", from_dt, to_dt)

    # --- System actions / warnings -------------------------------------

    def system_warnings(self, from_dt: datetime, to_dt: datetime) -> pd.DataFrame:
        """SYS_WARN: NESO system warnings (SO-SO trades, capacity, etc)."""
        return self.dataset("SYSWARN", from_dt, to_dt)

    def reserve_utilisation(self, from_dt: datetime, to_dt: datetime) -> pd.DataFrame:
        """RURE: reserve utilisation rates (response curve parameters)."""
        return self.dataset("RURE", from_dt, to_dt)


def settlement_period_to_time(date_str: str, settlement_period: int) -> datetime:
    """Map UK settlement period (1..48 normally) to a UTC datetime."""
    base = datetime.fromisoformat(date_str)
    return base + timedelta(minutes=30 * (settlement_period - 1))
