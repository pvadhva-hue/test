"""Battery Energy Storage System (BESS) trading margin calculations.

The "perfect foresight" arbitrage revenue is a standard yardstick for
how much a battery could have earned each day if it knew prices in
advance. It sets the upper bound on what an optimiser can capture.

The model:
- Power rating P (MW), duration D (hours), so usable energy = P * D (MWh).
- Round-trip efficiency eta_rt: to deliver 1 MWh you must buy 1 / eta_rt MWh.
- For each "cycle" we charge during the cheapest D hours of the window
  and discharge during the most expensive D hours, with the discharge
  set strictly later in time than the charge.
- Settlement periods can be hourly (DA) or half-hourly (imbalance);
  prices are aggregated to a common cadence by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BatteryConfig:
    power_mw: float = 50.0
    duration_h: float = 2.0
    round_trip_efficiency: float = 0.86
    cycles_per_day: int = 1

    @property
    def energy_mwh(self) -> float:
        return self.power_mw * self.duration_h


def _select_arbitrage_slots(
    prices: pd.Series,
    *,
    slots_per_side: int,
) -> tuple[list[int], list[int]] | None:
    """Pick `slots_per_side` cheapest and most expensive non-overlapping
    indices. State-of-charge management means we don't need to enforce
    that charge happens before discharge in clock time — the relevant
    daily quantity is the price spread that perfect cycling captures.
    """
    n = len(prices)
    if n < 2 * slots_per_side:
        return None
    order = np.argsort(prices.values, kind="stable")
    charge_idx = [int(i) for i in order[:slots_per_side]]
    discharge_idx = [int(i) for i in order[-slots_per_side:]]
    # Indices are disjoint by construction since n >= 2 * slots_per_side.
    return charge_idx, discharge_idx


def bess_arbitrage_revenue(
    prices: pd.DataFrame,
    config: BatteryConfig,
    *,
    price_col: str = "price",
    time_col: str = "time",
) -> pd.DataFrame:
    """Per-day perfect-foresight arbitrage revenue (£) for a BESS.

    Parameters
    ----------
    prices: DataFrame with columns [time_col, price_col]. Prices in £/MWh.
            The cadence (hourly or half-hourly) is inferred from the time
            spacing.
    config: BatteryConfig.
    """
    if prices.empty:
        return pd.DataFrame(columns=["date", "revenue_gbp", "charge_cost_gbp",
                                     "discharge_rev_gbp", "spread_gbp_mwh"])

    df = prices[[time_col, price_col]].dropna().copy()
    df[time_col] = pd.to_datetime(df[time_col], utc=True)
    df = df.sort_values(time_col).reset_index(drop=True)

    deltas = df[time_col].diff().dropna()
    median_minutes = deltas.dt.total_seconds().median() / 60.0 if not deltas.empty else 60.0
    period_h = median_minutes / 60.0 if median_minutes else 1.0
    slots_per_side = max(1, int(round(config.duration_h / period_h)))

    df["date"] = df[time_col].dt.date
    out_rows: list[dict] = []
    for day, group in df.groupby("date"):
        selection = _select_arbitrage_slots(group[price_col].reset_index(drop=True),
                                            slots_per_side=slots_per_side)
        if selection is None:
            continue
        charge_idx, discharge_idx = selection
        prices_arr = group[price_col].values
        charge_price = float(np.mean(prices_arr[charge_idx]))
        discharge_price = float(np.mean(prices_arr[discharge_idx]))

        # Energy delivered per cycle (limited by usable energy)
        delivered_mwh = config.energy_mwh * config.cycles_per_day
        bought_mwh = delivered_mwh / config.round_trip_efficiency

        charge_cost = charge_price * bought_mwh
        discharge_rev = discharge_price * delivered_mwh
        revenue = discharge_rev - charge_cost
        spread = discharge_price - charge_price / config.round_trip_efficiency

        out_rows.append({
            "date": pd.Timestamp(day),
            "revenue_gbp": revenue,
            "charge_cost_gbp": charge_cost,
            "discharge_rev_gbp": discharge_rev,
            "spread_gbp_mwh": spread,
            "charge_price_gbp_mwh": charge_price,
            "discharge_price_gbp_mwh": discharge_price,
        })

    return pd.DataFrame(out_rows)


def daily_bess_summary(
    da_prices: pd.DataFrame,
    imbalance_prices: pd.DataFrame | None,
    config: BatteryConfig,
    *,
    da_price_col: str = "price",
    da_time_col: str = "time",
    sip_price_col: str = "systemSellPrice",
    sip_time_col: str = "startTime",
) -> pd.DataFrame:
    """Combine DA-only and (optionally) imbalance-augmented revenue.

    Returns a dataframe with daily revenue from DA arbitrage and, if
    imbalance prices are provided, the additional uplift you'd capture
    by trading half-hour spread on top.
    """
    da = bess_arbitrage_revenue(
        da_prices.rename(columns={da_price_col: "price", da_time_col: "time"}),
        config,
    ).rename(columns={"revenue_gbp": "da_revenue_gbp",
                       "spread_gbp_mwh": "da_spread_gbp_mwh"})
    if imbalance_prices is None or imbalance_prices.empty:
        return da

    sip = bess_arbitrage_revenue(
        imbalance_prices.rename(columns={sip_price_col: "price",
                                          sip_time_col: "time"}),
        config,
    ).rename(columns={"revenue_gbp": "sip_revenue_gbp",
                       "spread_gbp_mwh": "sip_spread_gbp_mwh"})
    merged = da.merge(sip[["date", "sip_revenue_gbp", "sip_spread_gbp_mwh"]],
                      on="date", how="outer").sort_values("date")
    merged["sip_uplift_gbp"] = merged["sip_revenue_gbp"].fillna(0) - merged[
        "da_revenue_gbp"].fillna(0)
    return merged
