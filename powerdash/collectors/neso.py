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
RESOURCES = {
    # Day-Ahead Hourly Auction prices (N2EX / EPEX) historical archive
    "da_prices_hourly": "9b1a8e6f-2a3b-44c0-9a3b-7c8f0f3b5b8e",
    # Dynamic Containment / Moderation / Regulation (low & high) results
    "dc_dm_dr_results": "888e5be0-3a1f-4d2f-b66e-7e3d6d96a8e2",
    # Short Term Operating Reserve tendered results
    "stor_results": "65d0bf57-7c1b-44ed-b3a3-3e2b9a5a0e21",
    # Quick Reserve auction results
    "quick_reserve": "5664c4dd-c2fd-4e9a-b29b-79b9f8b8b35a",
    # Balancing Reserve auction results
    "balancing_reserve": "7b5e7b03-8a39-4f7f-9ec9-2d1f56c3b9f6",
    # Historic Demand Data
    "historic_demand": "bb44a1b5-75b1-4db2-8491-257f23385006",
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

    def ancillary_results(
        self,
        product: str = "dc",
        resource_id: str | None = None,
        limit: int = 2000,
    ) -> pd.DataFrame:
        """Frequency response auction results (DC/DM/DR low and high).

        ``product`` is matched against the dataset's "service" /
        "product" column. Pass a known resource id directly to query
        Quick Reserve or Balancing Reserve auctions:

        - Dynamic Containment: ``dc`` (low + high)
        - Dynamic Moderation:  ``dm``
        - Dynamic Regulation:  ``dr``
        - Quick Reserve:       resource ``quick_reserve``
        - Balancing Reserve:   resource ``balancing_reserve``
        """
        rid = resource_id or RESOURCES["dc_dm_dr_results"]
        df = self.datastore_search(rid, limit=limit)
        if df.empty:
            return df
        prod_cols = [c for c in df.columns if "product" in c.lower() or "service" in c.lower()]
        if prod_cols:
            mask = df[prod_cols[0]].astype(str).str.lower().str.contains(product.lower())
            df = df[mask]
        return df

    def quick_reserve_auctions(self, limit: int = 2000) -> pd.DataFrame:
        return self.datastore_search(RESOURCES["quick_reserve"], limit=limit)

    def balancing_reserve_auctions(self, limit: int = 2000) -> pd.DataFrame:
        return self.datastore_search(RESOURCES["balancing_reserve"], limit=limit)

    def historic_demand(self, limit: int = 5000) -> pd.DataFrame:
        return self.datastore_search(RESOURCES["historic_demand"], limit=limit)
