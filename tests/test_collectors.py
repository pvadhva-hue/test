"""Light smoke tests for collector wrappers (no network calls)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

import pandas as pd

from powerdash.collectors.elexon import ElexonClient, settlement_period_to_time
from powerdash.collectors.neso import NesoClient


def test_settlement_period_to_time():
    t = settlement_period_to_time("2024-06-01", 1)
    assert t == datetime(2024, 6, 1)
    t37 = settlement_period_to_time("2024-06-01", 37)
    assert t37.hour == 18 and t37.minute == 0


def test_elexon_records_to_df():
    fake = {
        "data": [
            {"startTime": "2024-06-01T00:00:00Z", "systemSellPrice": 50.0,
             "systemBuyPrice": 60.0},
            {"startTime": "2024-06-01T00:30:00Z", "systemSellPrice": 55.0,
             "systemBuyPrice": 65.0},
        ]
    }
    with patch("powerdash.collectors.elexon.get_json", return_value=fake):
        df = ElexonClient().system_prices(date(2024, 6, 1))
    assert not df.empty
    assert {"systemSellPrice", "systemBuyPrice"}.issubset(df.columns)
    assert pd.api.types.is_datetime64_any_dtype(df["startTime"])


def test_neso_datastore_search_empty_on_failure():
    with patch("powerdash.collectors.neso.get_json", return_value={"success": False}):
        df = NesoClient().datastore_search("nope")
    assert df.empty


def test_neso_datastore_search_records():
    fake = {
        "success": True,
        "result": {
            "records": [
                {"settlement_date": "2024-06-01", "price": 75.5},
                {"settlement_date": "2024-06-01", "price": 81.2},
            ]
        },
    }
    with patch("powerdash.collectors.neso.get_json", return_value=fake):
        df = NesoClient().datastore_search("any")
    assert len(df) == 2
    assert "price" in df.columns
