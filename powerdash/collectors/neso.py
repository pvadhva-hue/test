"""NESO Data Portal client (CKAN datastore API).

The portal at https://www.neso.energy/data-portal is a CKAN instance.
Each dataset has resources with a UUID `resource_id` that we query via
`datastore_search` and `datastore_search_sql`.

This module ships well-known resource IDs for the most useful market
datasets, but every method also accepts a `resource_id` override so the
dashboard can be repointed at successor resources without code changes.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from ._http import get_json


BASE_URL = "https://api.neso.energy/api/3/action"


# Curated resource IDs for the dashboards. These ids are stable but the
# NESO portal occasionally publishes new versions; override at the call
# site via the `resource_id` argument if needed.
# Curated resource IDs (verified live 2026-06-27). The NESO portal
# occasionally publishes new resource versions; override at the call
# site via the `resource_id` argument if needed.
RESOURCES = {
    # The single unified Enduring Auction Capability (EAC) results
    # summary — clearing price and cleared volume per product per EFA
    # window. Products: DCH/DCL/DMH/DML/DRH/DRL (Response) and
    # NBR/PBR/NQR/PQR/NSR/PSR (Reserve: Balancing / Quick / Slow,
    # Negative / Positive).
    "eac_results_summary": "596f29ac-0387-4ba4-a6d3-95c243140707",
    "eac_results_by_unit": "a63ab354-7e68-44c2-ad96-c6f920c30e85",
    "eac_buy_orders":      "1cf68f59-8eb8-4f1d-bccf-11b5a47b24e5",
    "eac_sell_orders":     "13b511df-d6ec-4143-afb1-0ecc6fd19810",
    # Quick / Balancing Reserve requirement forecasts
    "quick_reserve_forecast":     "f012de08-b258-408b-bc41-f885e183f97f",
    "balancing_reserve_forecast": "019f6fba-4f17-4056-9ef8-31df44ff2e30",
    # Non-BM reserve availability MW and utilisation price (OBP)
    "obp_reserve_avail": "6bfe7df0-60aa-462d-94cd-44ac0a4abb2c",
    # Pre-EAC archive (historical reference only)
    "dc_dm_dr_legacy_summary": "888e5029-f786-41d2-bc15-cbfd1d285e96",
    # Historic Demand Data
    "historic_demand": "bb44a1b5-75b1-4db2-8491-257f23385006",
}

# EAC product code -> human label / category
EAC_PRODUCTS = {
    "DCH": ("Dynamic Containment (High)", "Response"),
    "DCL": ("Dynamic Containment (Low)",  "Response"),
    "DMH": ("Dynamic Moderation (High)",  "Response"),
    "DML": ("Dynamic Moderation (Low)",   "Response"),
    "DRH": ("Dynamic Regulation (High)",  "Response"),
    "DRL": ("Dynamic Regulation (Low)",   "Response"),
    "PBR": ("Balancing Reserve (Pos)",    "Reserve"),
    "NBR": ("Balancing Reserve (Neg)",    "Reserve"),
    "PQR": ("Quick Reserve (Pos)",        "Reserve"),
    "NQR": ("Quick Reserve (Neg)",        "Reserve"),
    "PSR": ("Slow Reserve (Pos)",         "Reserve"),
    "NSR": ("Slow Reserve (Neg)",         "Reserve"),
}


class NesoClient:
    def __init__(self, base_url: str = BASE_URL, *, ttl_seconds: int = 1800):
        self.base_url = base_url.rstrip("/")
        self.ttl_seconds = ttl_seconds

    def datastore_search(
        self,
        resource_id: str,
        *,
        limit: int = 1000,
        filters: dict[str, Any] | None = None,
        q: str | None = None,
    ) -> pd.DataFrame:
        params: dict[str, Any] = {"resource_id": resource_id, "limit": limit}
        if filters:
            import json as _json

            params["filters"] = _json.dumps(filters)
        if q:
            params["q"] = q
        payload = get_json(
            f"{self.base_url}/datastore_search",
            params=params,
            ttl_seconds=self.ttl_seconds,
        )
        if not isinstance(payload, dict) or not payload.get("success"):
            return pd.DataFrame()
        records = payload.get("result", {}).get("records", [])
        return pd.DataFrame(records)

    def datastore_sql(self, sql: str) -> pd.DataFrame:
        """Run a parameter-less SQL query against the datastore."""
        payload = get_json(
            f"{self.base_url}/datastore_search_sql",
            params={"sql": sql},
            ttl_seconds=self.ttl_seconds,
        )
        if not isinstance(payload, dict) or not payload.get("success"):
            return pd.DataFrame()
        return pd.DataFrame(payload.get("result", {}).get("records", []))

    def resource_show(self, resource_id: str) -> dict[str, Any]:
        payload = get_json(
            f"{self.base_url}/resource_show",
            params={"id": resource_id},
            ttl_seconds=self.ttl_seconds,
        )
        if isinstance(payload, dict) and payload.get("success"):
            return payload.get("result", {})
        return {}

    # Convenience helpers ------------------------------------------------

    def da_prices(self, resource_id: str | None = None, limit: int = 2000) -> pd.DataFrame:
        rid = resource_id or RESOURCES["da_prices_hourly"]
        df = self.datastore_search(rid, limit=limit)
        for col in df.columns:
            if "date" in col.lower() or "time" in col.lower():
                df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
        return df

    def eac_auction_results(
        self,
        *,
        from_date: str | None = None,
        products: list[str] | None = None,
        limit: int = 50000,
    ) -> pd.DataFrame:
        """EAC summary clearing prices / volumes for response & reserve auctions.

        Returns one row per (auctionProduct, deliveryStart) with the
        clearing price (£/MW/h) and cleared volume (MW). Use the
        ``products`` filter to narrow to e.g. ``["DCH", "DCL"]``.
        """
        if from_date:
            cond = f"WHERE \"deliveryStart\" >= '{from_date}'"
        else:
            cond = ""
        prod_filter = ""
        if products:
            prods = ",".join(f"'{p}'" for p in products)
            prod_filter = (" AND " if cond else "WHERE ") + \
                f"\"auctionProduct\" IN ({prods})"
        sql = (f'SELECT "auctionProduct", "serviceType", "deliveryStart", '
               f'"deliveryEnd", "clearedVolume", "clearingPrice" '
               f'FROM "{RESOURCES["eac_results_summary"]}" '
               f'{cond}{prod_filter} '
               f'ORDER BY "deliveryStart" DESC LIMIT {limit}')
        df = self.datastore_sql(sql)
        if not df.empty:
            df["deliveryStart"] = pd.to_datetime(df["deliveryStart"],
                                                  utc=True, errors="coerce")
            df["deliveryEnd"] = pd.to_datetime(df["deliveryEnd"],
                                                utc=True, errors="coerce")
            for col in ("clearingPrice", "clearedVolume"):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df["productLabel"] = df["auctionProduct"].map(
                lambda p: EAC_PRODUCTS.get(p, (p, ""))[0])
        return df

    # Backwards-compatible single-product helpers ---------------------

    def ancillary_results(self, product: str = "dc", **_) -> pd.DataFrame:
        product = product.upper()
        codes = [p for p in EAC_PRODUCTS if p.startswith(product)]
        if not codes:
            codes = [product]
        return self.eac_auction_results(products=codes)

    def quick_reserve_auctions(self, **_) -> pd.DataFrame:
        return self.eac_auction_results(products=["PQR", "NQR"])

    def balancing_reserve_auctions(self, **_) -> pd.DataFrame:
        return self.eac_auction_results(products=["PBR", "NBR"])

    def historic_demand(self, limit: int = 5000) -> pd.DataFrame:
        return self.datastore_search(RESOURCES["historic_demand"], limit=limit)
