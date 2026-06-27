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
- Disaggregated BSAD (DISBSAD) by service
- NESO system warnings (BM System Action notices)
- Ancillary auctions (DC / DM / DR / Quick Reserve / Balancing Reserve)
  rendered only when the NESO portal is reachable

Usage:
    python -m scripts.build_dashboard [--date YYYY-MM-DD] [--out file.html]
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from powerdash.collectors import ElexonClient, NesoClient


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


def fetch(target: date) -> dict[str, pd.DataFrame]:
    el = ElexonClient()
    start = datetime(target.year, target.month, target.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    print(f"fetching for {target}...")
    out = {
        "system_prices": _safe(el.system_prices, target),
        "demand": _safe(el.demand_outturn, target),
        "generation": _safe(el.generation_by_fuel, start, end),
        "mid_epex": _safe(el.day_ahead_epex, start, end),
        "mid_n2ex": _safe(el.day_ahead_n2ex, start, end),
        "bod": _safe(el.bid_offer_data, start, end),
        "boalf": _safe(el.bid_offer_acceptances, start, end),
        "netbsad": _safe(el.net_bsad, start, end),
        "disbsad": _safe(el.disaggregated_bsad, start, end),
        "syswarn": _safe(el.system_warnings,
                          start - timedelta(days=1), end),
    }
    # NESO ancillary auctions — single EAC summary feed covers all
    # response / reserve products (DC, DM, DR, BR, QR, SR).
    try:
        neso = NesoClient()
        out["eac"] = _safe(
            neso.eac_auction_results,
            from_date=(target - timedelta(days=30)).isoformat(),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN: NESO unreachable: {exc}")
        out["eac"] = pd.DataFrame()
    return out


# ----- chart helpers -------------------------------------------------------

def _fig_price_demand_gen(data: dict[str, pd.DataFrame], target: date) -> go.Figure:
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
    fig.update_layout(
        title=dict(text=f"<b>{target} — system, demand & generation</b>",
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


def _fig_bsad(data: dict[str, pd.DataFrame]) -> go.Figure:
    """DISBSAD volumes & costs broken down by service."""
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        row_heights=[0.5, 0.5],
        subplot_titles=("DISBSAD volumes (MWh) by service",
                        "DISBSAD costs (£) by service"),
    )
    bsad = data["disbsad"]
    if bsad.empty:
        fig.add_annotation(text="DISBSAD: no data for window",
                            x=0.5, y=0.5, xref="paper", yref="paper",
                            showarrow=False)
    else:
        bsad = bsad.copy()
        # Map settlement period to a time stamp within the day
        if "settlementDate" in bsad.columns:
            bsad["startTime"] = pd.to_datetime(bsad["settlementDate"],
                                                utc=True, errors="coerce") + \
                pd.to_timedelta((bsad["settlementPeriod"] - 1) * 30, unit="m")
        else:
            bsad["startTime"] = pd.NaT
        bsad["service"] = bsad["service"].fillna("Unknown")
        vol = (bsad.groupby(["startTime", "service"])["volume"].sum()
                .unstack(fill_value=0))
        cost = (bsad.groupby(["startTime", "service"])["cost"].sum()
                 .unstack(fill_value=0))
        palette = ["#e74c3c", "#3498db", "#16a085", "#f39c12", "#8e44ad",
                   "#34495e", "#95a5a6"]
        for i, svc in enumerate(vol.columns):
            colour = palette[i % len(palette)]
            fig.add_trace(go.Bar(x=vol.index, y=vol[svc], name=f"vol {svc}",
                                  marker_color=colour,
                                  hovertemplate="%{x|%H:%M}<br>%{y:.1f} MWh<extra>"+svc+"</extra>"),
                          row=1, col=1)
            fig.add_trace(go.Bar(x=cost.index, y=cost[svc],
                                  name=f"cost {svc}", marker_color=colour,
                                  showlegend=False,
                                  hovertemplate="%{x|%H:%M}<br>£%{y:,.0f}<extra>"+svc+"</extra>"),
                          row=2, col=1)
        fig.update_layout(barmode="relative")

    fig.update_yaxes(title_text="MWh", row=1, col=1)
    fig.update_yaxes(title_text="£", row=2, col=1)
    fig.update_xaxes(title_text="Time (UTC)", row=2, col=1)
    fig.update_layout(
        title=dict(text="<b>BSAD — non-BM balancing volumes & costs by service</b>",
                    x=0.01, xanchor="left"),
        height=680, margin=dict(l=60, r=30, t=70, b=40),
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


def kpi_strip(data: dict[str, pd.DataFrame], target: date) -> str:
    sp = data["system_prices"]
    dem = data["demand"]
    gen = data["generation"]
    epex = data["mid_epex"]
    bsad = data["disbsad"]

    sells = sp.get("systemSellPrice", pd.Series(dtype=float)).dropna()
    p_mean = sells.mean() if len(sells) else float("nan")
    p_peak = sells.max() if len(sells) else float("nan")
    p_trough = sells.min() if len(sells) else float("nan")
    spread = (p_peak - p_trough) if len(sells) else float("nan")

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

    bsad_cost = pd.to_numeric(bsad["cost"], errors="coerce").sum() \
        if not bsad.empty else float("nan")

    cards = [
        ("Day mean SIP", f"£{p_mean:,.0f}/MWh"),
        ("Peak SIP", f"£{p_peak:,.0f}/MWh"),
        ("Intraday spread", f"£{spread:,.0f}/MWh"),
        ("EPEX DA mean", f"£{epex_mean:,.0f}/MWh"),
        ("Peak ITSDO", f"{d_peak:,.1f} GW"),
        ("Avg renewables share", f"{ren_pct:.1f}%"),
        ("Day BSAD cost", f"£{bsad_cost:,.0f}"),
    ]
    cards_html = "".join(
        f'<div class="kpi"><div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div></div>'
        for label, value in cards
    )
    return f'<div class="kpi-strip">{cards_html}</div>'


def render_warnings(data: dict[str, pd.DataFrame]) -> str:
    sw = data["syswarn"]
    if sw.empty:
        return ('<div class="card"><h3>System warnings (BM System Action)</h3>'
                '<p class="muted">No warnings in window.</p></div>')
    rows = []
    for _, r in sw.sort_values("publishTime", ascending=False).head(12).iterrows():
        txt = (str(r.get("warningText", "")) or "")[:600].replace("\r", "")
        rows.append(
            f'<tr><td class="nowrap">{r.get("publishTime","")}</td>'
            f'<td class="nowrap">{r.get("warningType","")}</td>'
            f'<td>{txt}</td></tr>'
        )
    return (
        '<div class="card"><h3>System warnings (BM System Action)</h3>'
        '<table class="warn"><thead><tr><th>Published</th>'
        '<th>Type</th><th>Text</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>UK Power Markets — {target}</title>
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
  <div class="sub">Settlement date {target} · Elexon BMRS Insights Solution{extra_sources}</div>
</header>
<main>
  {kpis}
  <div class="chart-card">{fig_main}</div>
  <div class="chart-card">{fig_wholesale}</div>
  <div class="chart-card">{fig_bm}</div>
  <div class="chart-card">{fig_bsad}</div>
  {fig_ancillary}
  {warnings}
</main>
<footer>Generated {generated_at} UTC · static HTML, no server required</footer>
</body>
</html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None,
                    help="Settlement date YYYY-MM-DD (default: yesterday UTC)")
    ap.add_argument("--out", default="data/cache/dashboard.html",
                    help="Output HTML path")
    args = ap.parse_args()

    target = (date.fromisoformat(args.date) if args.date
              else (datetime.now(timezone.utc) - timedelta(days=1)).date())
    data = fetch(target)

    main_fig = _fig_price_demand_gen(data, target).to_html(
        include_plotlyjs="cdn", full_html=False, div_id="dash-main",
        config={"displaylogo": False})
    wholesale_fig = _fig_wholesale(data).to_html(
        include_plotlyjs=False, full_html=False, div_id="dash-wholesale",
        config={"displaylogo": False})
    bm_fig = _fig_bm_bid_offer(data).to_html(
        include_plotlyjs=False, full_html=False, div_id="dash-bm",
        config={"displaylogo": False})
    bsad_fig = _fig_bsad(data).to_html(
        include_plotlyjs=False, full_html=False, div_id="dash-bsad",
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

    html = PAGE_TEMPLATE.format(
        target=target, extra_sources=extra_sources,
        kpis=kpi_strip(data, target),
        fig_main=main_fig, fig_wholesale=wholesale_fig,
        fig_bm=bm_fig, fig_bsad=bsad_fig, fig_ancillary=anc_html,
        warnings=render_warnings(data),
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
