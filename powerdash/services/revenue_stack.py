"""Battery revenue-stack calculator across UK power markets.

Combines potential earnings for a hypothetical BESS at settlement-period
(30 min) granularity across three market streams:

1. **Wholesale DA arbitrage** — perfect-foresight daily cycle over
   hourly EPEX / N2EX prices; the day's arbitrage revenue is spread
   evenly across the 48 half-hour settlement periods.
2. **Imbalance / BM uplift** — extra revenue from cycling on the
   sharper half-hourly system imbalance price on top of the DA cycle,
   floored at zero (imbalance cycling never *destroys* value on a
   perfect-foresight basis).
3. **Ancillary capacity revenue** — for each settlement period the
   best available clearing price × committed MW × 0.5 h across:
     - DC/DM/DR (4-hour EFA blocks: high & low, response services)
     - BR/QR/SR (30-minute settlement periods: positive & negative
       Balancing / Quick / Slow reserve)

A battery has to *choose* per settlement period: sell capacity into
ancillary OR run wholesale + BM arbitrage. We pick whichever pays
more, producing a "top-of-stack" schedule. The stack breakdown tells
you which market drove the money.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .bess import BatteryConfig, bess_arbitrage_revenue


SP_HOURS = 0.5
SPS_PER_DAY = 48
EFA_HOURS = 4

RESPONSE_PRODUCTS = ("DCH", "DCL", "DMH", "DML", "DRH", "DRL")
RESERVE_PRODUCTS = ("PBR", "NBR", "PQR", "NQR", "PSR", "NSR")


@dataclass(frozen=True)
class StackAssumptions:
    """Realistic capture rates for a GB 2h BESS operating a stacked strategy.

    ancillary_availability_pct — the share of the battery's nameplate
        MW that is committed to the highest-paying ancillary product at
        any given time. The rest of the MW is free to run wholesale +
        imbalance arbitrage. Modo Energy's 2025 GB BESS Index puts the
        typical split around 40–50% ancillary / 50–60% wholesale.
    da_capture — fraction of perfect-foresight DA arbitrage a real
        optimiser catches (typically 0.5–0.6 for a 2h battery).
    imbalance_capture — fraction of the (SIP − DA) daily uplift a real
        BM strategy monetises (typically 0.3–0.5).
    """
    ancillary_availability_pct: float = 0.50
    imbalance_capture: float = 0.4
    da_capture: float = 0.60
    include_products: tuple[str, ...] = RESPONSE_PRODUCTS + RESERVE_PRODUCTS


def _sp_grid(start: date, end: date) -> pd.DatetimeIndex:
    """All half-hour SP starts covering [start, end] inclusive (UTC)."""
    first = pd.Timestamp(start, tz="UTC") - pd.Timedelta(hours=1)  # SP 1 starts 23:00 previous day
    last = pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=23)
    return pd.date_range(first, last, freq="30min")


def _da_value_per_sp(da_prices: pd.DataFrame, cfg: BatteryConfig,
                     assump: StackAssumptions,
                     sp_grid: pd.DatetimeIndex) -> pd.DataFrame:
    """Spread DA arbitrage revenue evenly across each day's 48 SPs."""
    if da_prices.empty:
        return pd.DataFrame({"sp_start": sp_grid, "da_gbp": 0.0})
    daily = bess_arbitrage_revenue(
        da_prices.rename(columns={"startTime": "time", "price": "price"}),
        cfg,
    )
    if daily.empty:
        return pd.DataFrame({"sp_start": sp_grid, "da_gbp": 0.0})
    daily["date"] = pd.to_datetime(daily["date"], utc=True)
    # Attribute the day's arbitrage revenue to the 48 SPs whose start
    # falls within that UTC calendar day-of-settlement (SP 1 starts at
    # 23:00 the previous calendar day).
    df = pd.DataFrame({"sp_start": sp_grid})
    df["settlement_date"] = (df["sp_start"] + pd.Timedelta(hours=1)).dt.date
    per_day = daily.set_index(daily["date"].dt.date)["revenue_gbp"] \
        * assump.da_capture / SPS_PER_DAY
    df["da_gbp"] = df["settlement_date"].map(per_day).fillna(0.0)
    return df[["sp_start", "da_gbp"]]


def _bm_uplift_per_sp(sip_prices: pd.DataFrame, da_prices: pd.DataFrame,
                       cfg: BatteryConfig, assump: StackAssumptions,
                       sp_grid: pd.DatetimeIndex) -> pd.DataFrame:
    """Incremental value from cycling on half-hourly SIP over the DA cycle."""
    if sip_prices.empty:
        return pd.DataFrame({"sp_start": sp_grid, "bm_gbp": 0.0})
    sip_daily = bess_arbitrage_revenue(
        sip_prices.rename(columns={"startTime": "time",
                                    "systemSellPrice": "price"}),
        cfg,
    )
    da_daily = bess_arbitrage_revenue(
        da_prices.rename(columns={"startTime": "time", "price": "price"}), cfg
    )
    if sip_daily.empty:
        return pd.DataFrame({"sp_start": sp_grid, "bm_gbp": 0.0})
    merged = sip_daily[["date", "revenue_gbp"]].rename(
        columns={"revenue_gbp": "sip"}
    ).merge(
        da_daily[["date", "revenue_gbp"]].rename(columns={"revenue_gbp": "da"}),
        on="date", how="outer",
    )
    merged["uplift"] = ((merged["sip"].fillna(0) - merged["da"].fillna(0))
                        .clip(lower=0)) * assump.imbalance_capture
    per_day = (merged.set_index(pd.to_datetime(merged["date"]).dt.date)
                     ["uplift"] / SPS_PER_DAY)
    df = pd.DataFrame({"sp_start": sp_grid})
    df["settlement_date"] = (df["sp_start"] + pd.Timedelta(hours=1)).dt.date
    df["bm_gbp"] = df["settlement_date"].map(per_day).fillna(0.0)
    return df[["sp_start", "bm_gbp"]]


def _ancillary_best_per_sp(eac: pd.DataFrame, cfg: BatteryConfig,
                             assump: StackAssumptions,
                             sp_grid: pd.DatetimeIndex) -> pd.DataFrame:
    """Best ancillary £/SP available for each half-hour.

    Response products (DC/DM/DR) are auctioned at 4h EFA blocks. Every
    SP inside that block receives the same clearing price.

    Reserve products (BR/QR/SR) are auctioned per settlement period.
    """
    df = pd.DataFrame({"sp_start": sp_grid})
    df["product"] = None
    df["clearingPrice"] = np.nan
    df["ancillary_gbp"] = 0.0

    if eac is None or eac.empty:
        return df

    eac = eac[eac["auctionProduct"].isin(assump.include_products)].copy()
    if eac.empty:
        return df
    eac["deliveryStart"] = pd.to_datetime(eac["deliveryStart"], utc=True)
    eac["deliveryEnd"] = pd.to_datetime(eac["deliveryEnd"], utc=True)
    eac["clearingPrice"] = pd.to_numeric(eac["clearingPrice"], errors="coerce")

    # For each SP, find all product×block pairs whose delivery window
    # covers it, then pick the highest clearing price.
    sps = df["sp_start"].values
    prod_best_price = np.full(len(sps), np.nan)
    prod_best_code = np.array([None] * len(sps), dtype=object)

    # For efficient lookup, split into two sub-frames and vectorise.
    grid_ns = df["sp_start"].astype("int64").values

    for _, row in eac.iterrows():
        p = row["clearingPrice"]
        if not np.isfinite(p):
            continue
        s = np.datetime64(row["deliveryStart"].to_datetime64())
        e = np.datetime64(row["deliveryEnd"].to_datetime64())
        mask = (df["sp_start"].values >= s) & (df["sp_start"].values < e)
        # Where this product beats what we've seen, replace
        better = mask & (~np.isfinite(prod_best_price) | (p > prod_best_price))
        prod_best_price[better] = p
        prod_best_code[better] = row["auctionProduct"]

    df["clearingPrice"] = prod_best_price
    df["product"] = prod_best_code
    df["ancillary_gbp"] = (df["clearingPrice"].fillna(0)
                            * cfg.power_mw * assump.ancillary_availability_pct
                            * SP_HOURS)
    return df


def revenue_stack(
    da_prices: pd.DataFrame,
    sip_prices: pd.DataFrame,
    eac: pd.DataFrame,
    cfg: BatteryConfig,
    *,
    assumptions: StackAssumptions | None = None,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Return per-SP revenue stacked additively across markets.

    A fraction ``ancillary_availability_pct`` of the battery's nameplate
    MW earns ancillary capacity revenue at the best-clearing product for
    that SP. The remaining fraction runs wholesale DA + BM arbitrage.
    Only negative-clearing ancillary blocks are floored at zero — a real
    optimiser would exit them.
    """
    assump = assumptions or StackAssumptions()
    sp_grid = _sp_grid(start, end)

    # Full-battery arb baseline (bess service already applies MW * eta)
    da = _da_value_per_sp(da_prices, cfg, assump, sp_grid)
    bm = _bm_uplift_per_sp(sip_prices, da_prices, cfg, assump, sp_grid)
    anc = _ancillary_best_per_sp(eac, cfg, assump, sp_grid)

    df = da.merge(bm, on="sp_start").merge(
        anc[["sp_start", "product", "clearingPrice", "ancillary_gbp"]],
        on="sp_start",
    )

    a = assump.ancillary_availability_pct
    # Ancillary earnings only for positive clearing prices; negative
    # blocks would be skipped by a real optimiser.
    df["ancillary_gbp"] = (df["ancillary_gbp"].clip(lower=0)
                             * a)
    # DA / BM revenues scale with the fraction of MW left over
    df["da_gbp"] = df["da_gbp"] * (1 - a)
    df["bm_gbp"] = df["bm_gbp"] * (1 - a)
    df["total_gbp"] = df["ancillary_gbp"] + df["da_gbp"] + df["bm_gbp"]

    df["settlement_date"] = (df["sp_start"] + pd.Timedelta(hours=1)).dt.date
    df = df[(df["settlement_date"] >= start) & (df["settlement_date"] <= end)]
    return df.rename(columns={"product": "ancillary_product"}).reset_index(drop=True)


def daily_stack_summary(stack: pd.DataFrame) -> pd.DataFrame:
    """Roll per-SP rows up to per-day totals per market stream."""
    if stack.empty:
        return stack
    out = stack.groupby("settlement_date").agg(
        sps=("sp_start", "count"),
        ancillary_gbp=("ancillary_gbp", "sum"),
        da_gbp=("da_gbp", "sum"),
        bm_gbp=("bm_gbp", "sum"),
        total_gbp=("total_gbp", "sum"),
    ).reset_index().rename(columns={"settlement_date": "day"})
    return out
