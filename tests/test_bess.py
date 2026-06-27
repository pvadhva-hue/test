"""Tests for the BESS arbitrage service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from powerdash.services.bess import (
    BatteryConfig,
    bess_arbitrage_revenue,
    daily_bess_summary,
)


def _hourly_prices(day_low: float, day_high: float) -> pd.DataFrame:
    """24-hour sine wave price profile centred between low and high."""
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    hours = np.arange(24)
    # Cheap overnight, peak around 18:00
    profile = (day_low + day_high) / 2 + (day_high - day_low) / 2 * np.sin(
        (hours - 6) / 24 * 2 * np.pi
    )
    return pd.DataFrame({
        "time": [base + timedelta(hours=int(h)) for h in hours],
        "price": profile,
    })


def test_arbitrage_positive_for_typical_diurnal_curve():
    prices = _hourly_prices(20, 200)
    cfg = BatteryConfig(power_mw=10, duration_h=2, round_trip_efficiency=0.86,
                        cycles_per_day=1)
    out = bess_arbitrage_revenue(prices, cfg)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["revenue_gbp"] > 0
    # Discharge price should clearly beat charge price for a real spread
    assert row["discharge_price_gbp_mwh"] > row["charge_price_gbp_mwh"]


def test_revenue_scales_with_power():
    prices = _hourly_prices(20, 200)
    small = bess_arbitrage_revenue(
        prices, BatteryConfig(power_mw=10, duration_h=2)
    )["revenue_gbp"].iloc[0]
    big = bess_arbitrage_revenue(
        prices, BatteryConfig(power_mw=100, duration_h=2)
    )["revenue_gbp"].iloc[0]
    assert big == small * 10


def test_efficiency_reduces_revenue():
    prices = _hourly_prices(20, 200)
    perfect = bess_arbitrage_revenue(
        prices, BatteryConfig(round_trip_efficiency=1.0)
    )["revenue_gbp"].iloc[0]
    lossy = bess_arbitrage_revenue(
        prices, BatteryConfig(round_trip_efficiency=0.7)
    )["revenue_gbp"].iloc[0]
    assert lossy < perfect


def test_empty_input_returns_empty_frame():
    out = bess_arbitrage_revenue(pd.DataFrame(columns=["time", "price"]),
                                  BatteryConfig())
    assert out.empty


def test_daily_summary_merges_da_and_imbalance():
    da = _hourly_prices(30, 180)
    # Half-hourly imbalance with sharper peaks
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    sip = pd.DataFrame({
        "startTime": [base + timedelta(minutes=30 * i) for i in range(48)],
        "systemSellPrice": np.tile(_hourly_prices(10, 250)["price"].values, 2)[:48],
    })
    summary = daily_bess_summary(
        da, sip, BatteryConfig(power_mw=10, duration_h=2)
    )
    assert "da_revenue_gbp" in summary.columns
    assert "sip_revenue_gbp" in summary.columns
    assert len(summary) == 1
