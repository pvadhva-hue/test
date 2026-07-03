"""Render a self-contained interactive HTML dashboard.

Pulls live data from Elexon BMRS (and NESO portal where reachable) and
writes a single HTML file with hover/zoom/range-slider interactivity.
Open it in any browser — no server required.

Sections rendered:

- System imbalance price
- Demand outturn (INDO / ITSDO)
- Generation mix by fuel
- Wholesale Market Index (Elexon MID): EPEX (APX) and N2EX day-ahead
- Balancing Mechanism: bid / offer prices and acceptance volumes
- Ancillary auctions (DC / DM / DR / Quick Reserve / Balancing Reserve)
  rendered only when the NESO portal is reachable

Usage:
    python -m scripts.build_dashboard [--date YYYY-MM-DD] [--days N]
                                       [--out file.html]

--date is the anchor (default: yesterday UTC). --days is the width of
the rolling window ending on --date (default: 7).
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from powerdash.collectors import ElexonClient, NesoClient
from powerdash.services.bess import BatteryConfig
from powerdash.services.revenue_stack import (
    StackAssumptions, daily_stack_summary, revenue_stack,
)


FUEL_COLOURS = {
    "Fossil Gas": "#d35400",
    "Fossil Hard coal": "#34495e",
    "Fossil Oil": "#7f8c8d",
    "Nuclear": "#8e44ad",
    "Biomass": "#a04000",
    "Wind Onshore": "#2ecc71",
    "Wind Offshore": "#27ae60",
    "Solar": "#f1c40f",
    "Hydro Run-of-river and poundage": "#3498db",
    "Hydro Pumped Storage": "#5dade2",
    "Other": "#95a5a6",
}
FUEL_ORDER = [
    "Nuclear", "Biomass", "Fossil Hard coal", "Fossil Oil", "Fossil Gas",
    "Hydro Run-of-river and poundage", "Hydro Pumped Storage",
    "Wind Onshore", "Wind Offshore", "Solar", "Other",
]
RENEWABLES = {"Wind Onshore", "Wind Offshore", "Solar",
              "Hydro Run-of-river and poundage", "Hydro Pumped Storage",
              "Biomass"}


def _safe(callable_, *args, **kwargs) -> pd.DataFrame:
    try:
        return callable_(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN: {callable_.__qualname__} failed: {exc}")
        return pd.DataFrame()


def fetch(start_date: date, end_date: date) -> dict[str, pd.DataFrame]:
    """Pull the rolling window `[start_date, end_date]` inclusive.

    Endpoints that only accept a single settlement date (system prices,
    demand outturn) are looped per day and concatenated. Everything else
    takes a from/to range natively.
    """
    el = ElexonClient()
    start = datetime(start_date.year, start_date.month, start_date.day,
                     tzinfo=timezone.utc)
    end = datetime(end_date.year, end_date.month, end_date.day,
                   tzinfo=timezone.utc) + timedelta(days=1)
    print(f"fetching {start_date} → {end_date} ({(end_date - start_date).days + 1} days)...")

    # Per-day endpoints
    days = pd.date_range(start_date, end_date, freq="D").date
    sp_frames, dem_frames = [], []
    for d in days:
        sp_frames.append(_safe(el.system_prices, d))
        dem_frames.append(_safe(el.demand_outturn, d))
    system_prices = (pd.concat([f for f in sp_frames if not f.empty],
                                ignore_index=True)
                     if any(not f.empty for f in sp_frames) else pd.DataFrame())
    demand = (pd.concat([f for f in dem_frames if not f.empty],
                        ignore_index=True)
              if any(not f.empty for f in dem_frames) else pd.DataFrame())

    out = {
        "system_prices": system_prices,
        "demand": demand,
        "generation": _safe(el.generation_by_fuel, start, end),
        "mid_epex": _safe(el.day_ahead_epex, start, end),
        "mid_n2ex": _safe(el.day_ahead_n2ex, start, end),
        "bod": _safe(el.bid_offer_data, start, end),
        "boalf": _safe(el.bid_offer_acceptances, start, end),
    }
    # NESO ancillary auctions — single EAC summary feed covers all
    # response / reserve products (DC, DM, DR, BR, QR, SR). We fetch a
    # 30-day trailing history so the 7-day mean bar chart has context.
    try:
        neso = NesoClient()
        out["eac"] = _safe(
            neso.eac_auction_results,
            from_date=(start_date - timedelta(days=30)).isoformat(),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN: NESO unreachable: {exc}")
        out["eac"] = pd.DataFrame()
    return out


# ----- chart helpers -------------------------------------------------------

def _fig_price_demand_gen(data: dict[str, pd.DataFrame],
                            start_date: date, end_date: date) -> go.Figure:
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        row_heights=[0.3, 0.3, 0.4],
        subplot_titles=("System imbalance price",
                        "Demand outturn (INDO / ITSDO)",
                        "Generation mix by fuel"),
    )
    sp = data["system_prices"].sort_values("startTime") if not data["system_prices"].empty else pd.DataFrame()
    if not sp.empty:
        fig.add_trace(go.Scatter(
            x=sp["startTime"], y=sp["systemSellPrice"],
            mode="lines+markers", name="System price",
            line=dict(color="#c0392b", width=2), marker=dict(size=4),
            hovertemplate="%{x|%H:%M}<br>£%{y:.2f}/MWh<extra></extra>",
        ), row=1, col=1)
        fig.add_hline(y=float(sp["systemSellPrice"].mean()),
                      line=dict(color="#7f8c8d", dash="dash", width=1),
                      row=1, col=1,
                      annotation_text=f"mean £{sp['systemSellPrice'].mean():.0f}",
                      annotation_position="right")

    dem = data["demand"].sort_values("startTime") if not data["demand"].empty else pd.DataFrame()
    if "initialDemandOutturn" in dem.columns:
        fig.add_trace(go.Scatter(
            x=dem["startTime"], y=dem["initialDemandOutturn"] / 1000,
            mode="lines", name="INDO", line=dict(color="#2980b9", width=2),
            hovertemplate="%{x|%H:%M}<br>%{y:.1f} GW (INDO)<extra></extra>",
        ), row=2, col=1)
    if "initialTransmissionSystemDemandOutturn" in dem.columns:
        fig.add_trace(go.Scatter(
            x=dem["startTime"],
            y=dem["initialTransmissionSystemDemandOutturn"] / 1000,
            mode="lines", name="ITSDO",
            line=dict(color="#1abc9c", width=2, dash="dash"),
            hovertemplate="%{x|%H:%M}<br>%{y:.1f} GW (ITSDO)<extra></extra>",
        ), row=2, col=1)

    gen = data["generation"]
    if not gen.empty:
        pivot = (gen.pivot_table(index="startTime", columns="psrType",
                                  values="quantity", aggfunc="sum")
                    .fillna(0) / 1000)
        for fuel in [f for f in FUEL_ORDER if f in pivot.columns]:
            fig.add_trace(go.Scatter(
                x=pivot.index, y=pivot[fuel],
                mode="lines", name=fuel, stackgroup="gen",
                line=dict(width=0.3, color=FUEL_COLOURS.get(fuel, "#bdc3c7")),
                fillcolor=FUEL_COLOURS.get(fuel, "#bdc3c7"),
                hovertemplate=f"{fuel}<br>%{{x|%H:%M}}<br>%{{y:.2f}} GW<extra></extra>",
            ), row=3, col=1)

    fig.update_yaxes(title_text="£/MWh", row=1, col=1)
    fig.update_yaxes(title_text="GW", row=2, col=1)
    fig.update_yaxes(title_text="GW", row=3, col=1)
    fig.update_xaxes(title_text="Time (UTC)", row=3, col=1,
                     rangeslider=dict(visible=True, thickness=0.04))
    label = (f"{start_date}" if start_date == end_date
             else f"{start_date} → {end_date}")
    fig.update_layout(
        title=dict(text=f"<b>{label} — system, demand & generation</b>",
                    x=0.01, xanchor="left"),
        height=860, margin=dict(l=60, r=30, t=60, b=40),
        hovermode="x unified", legend=dict(orientation="h", y=-0.06,
            yanchor="top", x=0.5, xanchor="center", font=dict(size=10)),
        template="plotly_white",
    )
    return fig


def _fig_wholesale(data: dict[str, pd.DataFrame]) -> go.Figure:
    fig = go.Figure()
    epex = data["mid_epex"]
    n2ex = data["mid_n2ex"]
    if not epex.empty:
        fig.add_trace(go.Scatter(
            x=epex["startTime"], y=epex["price"], mode="lines+markers",
            name="EPEX day-ahead (APXMIDP)", line=dict(color="#9b59b6", width=2),
            marker=dict(size=5),
            hovertemplate="%{x|%H:%M}<br>£%{y:.2f}/MWh<br>%{customdata:,.0f} MWh<extra>EPEX</extra>",
            customdata=epex["volume"].fillna(0),
        ))
    if not n2ex.empty:
        fig.add_trace(go.Scatter(
            x=n2ex["startTime"], y=n2ex["price"], mode="lines+markers",
            name="N2EX day-ahead (N2EXMIDP)",
            line=dict(color="#16a085", width=2, dash="dash"),
            marker=dict(size=5),
            hovertemplate="%{x|%H:%M}<br>£%{y:.2f}/MWh<extra>N2EX</extra>",
        ))
    fig.update_layout(
        title=dict(text="<b>Wholesale Market Index — day-ahead reference (EPEX vs N2EX)</b>",
                    x=0.01, xanchor="left"),
        height=380, margin=dict(l=60, r=30, t=60, b=40),
        xaxis_title="Time (UTC)", yaxis_title="£/MWh",
        hovermode="x unified", template="plotly_white",
        legend=dict(orientation="h", y=-0.18, x=0.5, xanchor="center"),
    )
    return fig


def _fig_bm_bid_offer(data: dict[str, pd.DataFrame]) -> go.Figure:
    """BOD (prices) and BOALF (acceptance volumes) by settlement period."""
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        row_heights=[0.55, 0.45],
        subplot_titles=("BOD: balancing bid / offer price distribution per period",
                        "BOALF: accepted volume per period (BM acceptances)"),
    )
    bod = data["bod"]
    if not bod.empty:
        bod = bod.copy()
        bod["startTime"] = pd.to_datetime(bod["timeFrom"], utc=True, errors="coerce")
        bod = bod[(bod["levelFrom"] != 0) | (bod["levelTo"] != 0)]
        # offer price summary per period
        per = bod.groupby("startTime").agg(
            offer_min=("offer", "min"), offer_med=("offer", "median"),
            offer_max=("offer", "max"),
            bid_min=("bid", "min"), bid_med=("bid", "median"),
            bid_max=("bid", "max"),
        ).reset_index()
        fig.add_trace(go.Scatter(x=per["startTime"], y=per["offer_max"],
            mode="lines", name="offer max", line=dict(color="#e74c3c", width=1),
            hovertemplate="%{x|%H:%M}<br>£%{y:.0f}/MWh<extra>offer max</extra>"),
            row=1, col=1)
        fig.add_trace(go.Scatter(x=per["startTime"], y=per["offer_min"],
            mode="lines", name="offer band",
            fill="tonexty", fillcolor="rgba(231,76,60,0.15)",
            line=dict(color="#e74c3c", width=1),
            hovertemplate="%{x|%H:%M}<br>£%{y:.0f}/MWh<extra>offer min</extra>"),
            row=1, col=1)
        fig.add_trace(go.Scatter(x=per["startTime"], y=per["offer_med"],
            mode="lines+markers", name="offer median",
            line=dict(color="#c0392b", width=2),
            hovertemplate="%{x|%H:%M}<br>£%{y:.0f}/MWh<extra>offer median</extra>"),
            row=1, col=1)
        fig.add_trace(go.Scatter(x=per["startTime"], y=per["bid_max"],
            mode="lines", name="bid max", line=dict(color="#3498db", width=1),
            hovertemplate="%{x|%H:%M}<br>£%{y:.0f}/MWh<extra>bid max</extra>"),
            row=1, col=1)
        fig.add_trace(go.Scatter(x=per["startTime"], y=per["bid_min"],
            mode="lines", name="bid band",
            fill="tonexty", fillcolor="rgba(52,152,219,0.15)",
            line=dict(color="#3498db", width=1),
            hovertemplate="%{x|%H:%M}<br>£%{y:.0f}/MWh<extra>bid min</extra>"),
            row=1, col=1)
        fig.add_trace(go.Scatter(x=per["startTime"], y=per["bid_med"],
            mode="lines+markers", name="bid median",
            line=dict(color="#2980b9", width=2),
            hovertemplate="%{x|%H:%M}<br>£%{y:.0f}/MWh<extra>bid median</extra>"),
            row=1, col=1)

    boalf = data["boalf"]
    if not boalf.empty:
        boalf = boalf.copy()
        boalf["startTime"] = pd.to_datetime(boalf["timeFrom"], utc=True, errors="coerce")
        boalf["mw"] = pd.to_numeric(boalf["levelTo"], errors="coerce").fillna(0)
        offers = boalf[boalf["mw"] > 0].groupby("startTime")["mw"].sum()
        bids = boalf[boalf["mw"] < 0].groupby("startTime")["mw"].sum()
        if not offers.empty:
            fig.add_trace(go.Bar(x=offers.index, y=offers.values,
                                  name="Accepted Offers", marker_color="#e74c3c",
                                  hovertemplate="%{x|%H:%M}<br>%{y:.0f} MW<extra>accepted offers</extra>"),
                          row=2, col=1)
        if not bids.empty:
            fig.add_trace(go.Bar(x=bids.index, y=bids.values,
                                  name="Accepted Bids", marker_color="#3498db",
                                  hovertemplate="%{x|%H:%M}<br>%{y:.0f} MW<extra>accepted bids</extra>"),
                          row=2, col=1)
        fig.update_layout(barmode="relative")

    fig.update_yaxes(title_text="£/MWh", row=1, col=1)
    fig.update_yaxes(title_text="MW (positive = offers, negative = bids)", row=2, col=1)
    fig.update_xaxes(title_text="Time (UTC)", row=2, col=1)
    fig.update_layout(
        title=dict(text="<b>Balancing Mechanism — Bids, Offers & Acceptances</b>",
                    x=0.01, xanchor="left"),
        height=720, margin=dict(l=60, r=30, t=70, b=40),
        hovermode="x unified", template="plotly_white",
        legend=dict(orientation="h", y=-0.12, x=0.5, xanchor="center", font=dict(size=10)),
    )
    return fig


PRODUCT_COLOURS = {
    # Response colours (red family — high vs low frequency)
    "DCH": "#c0392b", "DCL": "#e67e22",
    "DMH": "#d35400", "DML": "#e74c3c",
    "DRH": "#8e44ad", "DRL": "#9b59b6",
    # Reserve colours (blue/green family)
    "PBR": "#2980b9", "NBR": "#3498db",
    "PQR": "#16a085", "NQR": "#1abc9c",
    "PSR": "#34495e", "NSR": "#7f8c8d",
}


def _fig_ancillary(data: dict[str, pd.DataFrame]) -> go.Figure | None:
    """EAC clearing prices for all response & reserve products."""
    eac = data.get("eac")
    if eac is None or eac.empty:
        return None

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1,
        subplot_titles=(
            "Frequency response auctions (DC / DM / DR — High & Low)",
            "Reserve auctions (Balancing / Quick / Slow — Positive & Negative)",
        ),
    )
    response = ["DCH", "DCL", "DMH", "DML", "DRH", "DRL"]
    reserve = ["PBR", "NBR", "PQR", "NQR", "PSR", "NSR"]

    for code in response:
        df = (eac[eac["auctionProduct"] == code]
               .sort_values("deliveryStart"))
        if df.empty:
            continue
        fig.add_trace(go.Scatter(
            x=df["deliveryStart"], y=df["clearingPrice"],
            mode="lines+markers", name=code,
            line=dict(color=PRODUCT_COLOURS.get(code, "#999"), width=1.6),
            marker=dict(size=4),
            customdata=df["clearedVolume"],
            hovertemplate=(f"{code} — {df['productLabel'].iloc[0]}<br>"
                            "%{x|%Y-%m-%d %H:%M}<br>"
                            "£%{y:.2f}/MW/h<br>%{customdata:.0f} MW<extra></extra>"),
        ), row=1, col=1)

    for code in reserve:
        df = (eac[eac["auctionProduct"] == code]
               .sort_values("deliveryStart"))
        if df.empty:
            continue
        fig.add_trace(go.Scatter(
            x=df["deliveryStart"], y=df["clearingPrice"],
            mode="lines+markers", name=code,
            line=dict(color=PRODUCT_COLOURS.get(code, "#999"), width=1.6),
            marker=dict(size=4),
            customdata=df["clearedVolume"],
            hovertemplate=(f"{code} — {df['productLabel'].iloc[0]}<br>"
                            "%{x|%Y-%m-%d %H:%M}<br>"
                            "£%{y:.2f}/MW/h<br>%{customdata:.0f} MW<extra></extra>"),
        ), row=2, col=1)

    fig.update_yaxes(title_text="£/MW/h", row=1, col=1)
    fig.update_yaxes(title_text="£/MW/h", row=2, col=1)
    fig.update_xaxes(title_text="Delivery start (UTC)", row=2, col=1)
    fig.update_layout(
        title=dict(text="<b>Ancillary auction clearing prices — NESO EAC</b>",
                    x=0.01, xanchor="left"),
        height=720, margin=dict(l=60, r=30, t=70, b=40),
        hovermode="x unified", template="plotly_white",
        legend=dict(orientation="h", y=-0.12, x=0.5, xanchor="center",
                    font=dict(size=10)),
    )
    return fig


def _fig_ancillary_summary(data: dict[str, pd.DataFrame]) -> go.Figure | None:
    """Latest 7-day average clearing price by product."""
    eac = data.get("eac")
    if eac is None or eac.empty:
        return None
    cutoff = eac["deliveryStart"].max() - pd.Timedelta(days=7)
    recent = eac[eac["deliveryStart"] >= cutoff].copy()
    if recent.empty:
        return None
    summary = (recent.groupby("auctionProduct")
                .agg(mean_price=("clearingPrice", "mean"),
                      mean_volume=("clearedVolume", "mean"))
                .reindex([c for c in PRODUCT_COLOURS if c in
                          recent["auctionProduct"].unique()]))
    fig = go.Figure(go.Bar(
        x=summary.index, y=summary["mean_price"],
        marker_color=[PRODUCT_COLOURS.get(c, "#999") for c in summary.index],
        customdata=summary["mean_volume"],
        text=[f"£{v:.1f}" for v in summary["mean_price"]],
        textposition="outside",
        hovertemplate="%{x}<br>mean £%{y:.2f}/MW/h<br>"
                       "%{customdata:.0f} MW avg cleared<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text="<b>7-day mean clearing price by product</b>",
                    x=0.01, xanchor="left"),
        height=320, margin=dict(l=60, r=30, t=60, b=40),
        yaxis_title="£/MW/h", template="plotly_white",
    )
    return fig


def _fig_revenue_stack(data: dict[str, pd.DataFrame],
                        start_date: date, end_date: date,
                        cfg: BatteryConfig,
                        assump: StackAssumptions) -> tuple[go.Figure | None,
                                                             pd.DataFrame,
                                                             pd.DataFrame]:
    """BESS revenue stack over the window: daily bars + product mix.

    Returns (figure, per-SP stack df, per-day summary df).
    """
    da = data["mid_epex"].copy()
    if not da.empty and "startTime" in da.columns and "price" in da.columns:
        da = da[["startTime", "price"]].dropna()
    else:
        da = pd.DataFrame(columns=["startTime", "price"])

    sip = data["system_prices"]
    eac = data.get("eac", pd.DataFrame())

    stack = revenue_stack(da, sip, eac, cfg, assumptions=assump,
                           start=start_date, end=end_date)
    summary = daily_stack_summary(stack)
    if summary.empty:
        return None, stack, summary

    fig = make_subplots(
        rows=1, cols=2, column_widths=[0.62, 0.38],
        specs=[[{"type": "bar"}, {"type": "domain"}]],
        subplot_titles=("Daily revenue by market",
                        "Ancillary product mix (SPs won)"),
    )
    fig.add_trace(go.Bar(
        x=summary["day"], y=summary["ancillary_gbp"], name="Ancillary",
        marker_color="#8e44ad",
        hovertemplate="%{x}<br>£%{y:,.0f}<extra>Ancillary</extra>",
    ), row=1, col=1)
    fig.add_trace(go.Bar(
        x=summary["day"], y=summary["da_gbp"], name="Wholesale DA",
        marker_color="#2980b9",
        hovertemplate="%{x}<br>£%{y:,.0f}<extra>Wholesale DA</extra>",
    ), row=1, col=1)
    fig.add_trace(go.Bar(
        x=summary["day"], y=summary["bm_gbp"], name="Imbalance / BM",
        marker_color="#e67e22",
        hovertemplate="%{x}<br>£%{y:,.0f}<extra>Imbalance / BM</extra>",
    ), row=1, col=1)

    if not stack.empty:
        # Ancillary revenue by product (only positive-clearing SPs get chosen)
        winners = stack[stack["ancillary_gbp"] > 0]
        mix = winners.groupby("ancillary_product")["ancillary_gbp"].sum() \
                     .sort_values(ascending=False)
        colours = [PRODUCT_COLOURS.get(str(p), "#999") for p in mix.index]
        fig.add_trace(go.Pie(
            labels=mix.index, values=mix.values, hole=0.4,
            marker=dict(colors=colours),
            textinfo="label+percent",
            hovertemplate="%{label}<br>£%{value:,.0f}<extra></extra>",
        ), row=1, col=2)

    total = summary["total_gbp"].sum()
    per_mw_day = total / cfg.power_mw / max(len(summary), 1)
    subtitle = (f"{cfg.power_mw:.0f} MW / {cfg.duration_h:g}h · "
                f"η<sub>rt</sub>={cfg.round_trip_efficiency:.0%} · "
                f"anc avail {assump.ancillary_availability_pct:.0%} · "
                f"DA capture {assump.da_capture:.0%} · "
                f"BM capture {assump.imbalance_capture:.0%}<br>"
                f"7-day total £{total:,.0f} "
                f"(£{per_mw_day:,.0f}/MW/day · "
                f"~£{per_mw_day * 365 / 1000:,.0f}k/MW/yr annualised)")
    fig.update_layout(
        title=dict(text=f"<b>BESS revenue stack</b><br>"
                         f"<sup>{subtitle}</sup>",
                    x=0.01, xanchor="left"),
        barmode="stack", height=460,
        margin=dict(l=60, r=30, t=90, b=40),
        template="plotly_white",
        legend=dict(orientation="h", y=-0.15, x=0.25, xanchor="center"),
    )
    fig.update_yaxes(title_text="£ / day", row=1, col=1)
    return fig, stack, summary


def kpi_strip(data: dict[str, pd.DataFrame],
              start_date: date, end_date: date) -> str:
    sp = data["system_prices"]
    dem = data["demand"]
    gen = data["generation"]
    epex = data["mid_epex"]
    days = (end_date - start_date).days + 1

    sells = sp.get("systemSellPrice", pd.Series(dtype=float)).dropna()
    p_mean = sells.mean() if len(sells) else float("nan")
    p_peak = sells.max() if len(sells) else float("nan")
    p_trough = sells.min() if len(sells) else float("nan")

    # Best daily arbitrage spread (mean top-4 vs bottom-4 SPs, ~2h battery)
    daily_spread = float("nan")
    if not sp.empty and "startTime" in sp.columns:
        sp2 = sp.copy()
        sp2["startTime"] = pd.to_datetime(sp2["startTime"], utc=True, errors="coerce")
        sp2["day"] = sp2["startTime"].dt.date
        spreads = []
        for _, g in sp2.groupby("day"):
            s = g["systemSellPrice"].dropna().sort_values()
            if len(s) >= 8:
                spreads.append(s.tail(4).mean() - s.head(4).mean())
        if spreads:
            daily_spread = sum(spreads) / len(spreads)

    epex_mean = pd.to_numeric(epex.get("price", pd.Series()), errors="coerce").mean() \
        if not epex.empty else float("nan")

    d_peak = pd.to_numeric(
        dem.get("initialTransmissionSystemDemandOutturn", pd.Series()),
        errors="coerce").max() / 1000 if not dem.empty else float("nan")

    if not gen.empty:
        total_avg = gen.groupby("startTime")["quantity"].sum().mean() / 1000
        ren_avg = (gen[gen["psrType"].isin(RENEWABLES)]
                   .groupby("startTime")["quantity"].sum().mean() / 1000)
        ren_pct = (ren_avg / total_avg * 100) if total_avg else float("nan")
    else:
        ren_pct = float("nan")

    prefix = f"{days}d" if days > 1 else "Day"
    cards = [
        (f"{prefix} mean SIP", f"£{p_mean:,.0f}/MWh"),
        (f"{prefix} peak SIP", f"£{p_peak:,.0f}/MWh"),
        (f"{prefix} trough SIP", f"£{p_trough:,.0f}/MWh"),
        ("Avg daily 2h spread", f"£{daily_spread:,.0f}/MWh"),
        ("EPEX DA mean", f"£{epex_mean:,.0f}/MWh"),
        ("Peak ITSDO", f"{d_peak:,.1f} GW"),
        ("Avg renewables share", f"{ren_pct:.1f}%"),
    ]
    cards_html = "".join(
        f'<div class="kpi"><div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div></div>'
        for label, value in cards
    )
    return f'<div class="kpi-strip">{cards_html}</div>'


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>UK Power Markets — {range_label}</title>
<style>
  :root {{ --bg:#f7f9fc; --card:#fff; --text:#1f2d3d; --muted:#6b7c93;
    --accent:#2c3e50; --border:#e3e8ee; }}
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',
    Roboto,sans-serif; background:var(--bg); color:var(--text); }}
  header {{ background:var(--accent); color:#fff; padding:20px 32px; }}
  header h1 {{ margin:0; font-size:20px; font-weight:600; }}
  header .sub {{ font-size:13px; opacity:0.75; margin-top:4px; }}
  main {{ max-width:1400px; margin:0 auto; padding:24px; }}
  .kpi-strip {{ display:grid;
    grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
    gap:12px; margin-bottom:20px; }}
  .kpi {{ background:var(--card); border:1px solid var(--border);
    border-radius:8px; padding:14px 16px; }}
  .kpi-label {{ color:var(--muted); font-size:12px; text-transform:uppercase;
    letter-spacing:0.04em; }}
  .kpi-value {{ font-size:22px; font-weight:600; margin-top:6px; }}
  .chart-card, .card {{ background:var(--card); border:1px solid var(--border);
    border-radius:8px; padding:8px; margin-bottom:18px; }}
  .card {{ padding:16px 20px; }}
  .card h3 {{ margin:0 0 10px 0; font-size:15px; color:var(--accent); }}
  .muted {{ color:var(--muted); font-size:13px; }}
  table.warn {{ width:100%; border-collapse:collapse; font-size:12px; }}
  table.warn th, table.warn td {{ text-align:left; padding:6px 8px;
    border-bottom:1px solid var(--border); vertical-align:top; }}
  table.warn th {{ background:#f1f4f8; color:var(--muted);
    font-weight:600; text-transform:uppercase; font-size:11px; }}
  td.nowrap {{ white-space:nowrap; color:var(--muted); }}
  footer {{ text-align:center; color:var(--muted); font-size:12px;
    padding:24px; }}
</style>
</head>
<body>
<header>
  <h1>UK Power Markets Dashboard</h1>
  <div class="sub">{range_label} · Elexon BMRS Insights Solution{extra_sources}</div>
</header>
<main>
  {kpis}
  <div class="chart-card">{fig_main}</div>
  <div class="chart-card">{fig_wholesale}</div>
  <div class="chart-card">{fig_bm}</div>
  {fig_ancillary}
  {fig_stack}
</main>
<footer>Generated {generated_at} UTC · static HTML, no server required</footer>
</body>
</html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None,
                    help="End date YYYY-MM-DD (default: yesterday UTC)")
    ap.add_argument("--days", type=int, default=7,
                    help="Width of the rolling window in days (default: 7)")
    ap.add_argument("--out", default="data/cache/dashboard.html",
                    help="Output HTML path")
    ap.add_argument("--battery-mw", type=float, default=50.0,
                    help="Battery power for the revenue-stack panel (MW)")
    ap.add_argument("--battery-hours", type=float, default=2.0,
                    help="Battery duration for the revenue-stack panel (h)")
    ap.add_argument("--battery-rte", type=float, default=0.86,
                    help="Round-trip efficiency for the revenue-stack panel")
    ap.add_argument("--ancillary-avail", type=float, default=0.50,
                    help="Fraction of MW committed to ancillary (0-1)")
    ap.add_argument("--da-capture", type=float, default=0.60,
                    help="Realised fraction of perfect-foresight DA arb (0-1)")
    ap.add_argument("--bm-capture", type=float, default=0.40,
                    help="Realised fraction of imbalance uplift (0-1)")
    args = ap.parse_args()

    end_date = (date.fromisoformat(args.date) if args.date
                else (datetime.now(timezone.utc) - timedelta(days=1)).date())
    days = max(1, int(args.days))
    start_date = end_date - timedelta(days=days - 1)
    data = fetch(start_date, end_date)
    range_label = (f"{end_date}" if start_date == end_date
                   else f"{start_date} → {end_date} ({days} days)")

    main_fig = _fig_price_demand_gen(data, start_date, end_date).to_html(
        include_plotlyjs="cdn", full_html=False, div_id="dash-main",
        config={"displaylogo": False})
    wholesale_fig = _fig_wholesale(data).to_html(
        include_plotlyjs=False, full_html=False, div_id="dash-wholesale",
        config={"displaylogo": False})
    bm_fig = _fig_bm_bid_offer(data).to_html(
        include_plotlyjs=False, full_html=False, div_id="dash-bm",
        config={"displaylogo": False})
    anc = _fig_ancillary(data)
    anc_sum = _fig_ancillary_summary(data)
    if anc is not None:
        parts = []
        if anc_sum is not None:
            parts.append('<div class="chart-card">' +
                         anc_sum.to_html(include_plotlyjs=False,
                                          full_html=False, div_id="dash-anc-sum",
                                          config={"displaylogo": False}) +
                         '</div>')
        parts.append('<div class="chart-card">' +
                     anc.to_html(include_plotlyjs=False, full_html=False,
                                  div_id="dash-anc",
                                  config={"displaylogo": False}) +
                     '</div>')
        anc_html = "".join(parts)
        extra_sources = " · NESO portal (EAC)"
    else:
        anc_html = ('<div class="card"><h3>Ancillary auctions '
                    '(DC / DM / DR / Quick Reserve / Balancing Reserve)</h3>'
                    '<p class="muted">NESO portal not reachable from this '
                    'environment — run locally to populate.</p></div>')
        extra_sources = ""

    cfg = BatteryConfig(power_mw=args.battery_mw,
                         duration_h=args.battery_hours,
                         round_trip_efficiency=args.battery_rte)
    assump = StackAssumptions(
        ancillary_availability_pct=args.ancillary_avail,
        da_capture=args.da_capture,
        imbalance_capture=args.bm_capture,
    )
    stack_fig, _, _ = _fig_revenue_stack(data, start_date, end_date, cfg, assump)
    if stack_fig is not None:
        stack_html = ('<div class="chart-card">' +
                       stack_fig.to_html(include_plotlyjs=False,
                                          full_html=False,
                                          div_id="dash-stack",
                                          config={"displaylogo": False}) +
                       '</div>')
    else:
        stack_html = ""

    html = PAGE_TEMPLATE.format(
        range_label=range_label, extra_sources=extra_sources,
        kpis=kpi_strip(data, start_date, end_date),
        fig_main=main_fig, fig_wholesale=wholesale_fig,
        fig_bm=bm_fig, fig_ancillary=anc_html, fig_stack=stack_html,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
