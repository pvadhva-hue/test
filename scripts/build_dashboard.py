"""Render a self-contained interactive HTML dashboard.

Pulls live data from Elexon BMRS and writes a single HTML file with
hover/zoom/range-slider interactivity. Open it in any browser — no
server required.

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

from powerdash.collectors import ElexonClient


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


def fetch(target: date) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    el = ElexonClient()
    sp = el.system_prices(target)
    dem = el.demand_outturn(target)
    start = datetime(target.year, target.month, target.day, tzinfo=timezone.utc)
    gen = el.generation_by_fuel(start, start + timedelta(days=1))
    return sp, dem, gen


def build_figure(sp: pd.DataFrame, dem: pd.DataFrame, gen: pd.DataFrame,
                  target: date) -> go.Figure:
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[0.28, 0.28, 0.44],
        subplot_titles=("System imbalance price",
                        "Demand outturn (INDO / ITSDO)",
                        "Generation mix by fuel"),
    )

    # --- System price -----------------------------------------------------
    sp = sp.sort_values("startTime")
    fig.add_trace(
        go.Scatter(
            x=sp["startTime"], y=sp["systemSellPrice"],
            mode="lines+markers", name="System price",
            line=dict(color="#c0392b", width=2),
            marker=dict(size=4),
            hovertemplate="%{x|%H:%M}<br>£%{y:.2f}/MWh<extra></extra>",
        ),
        row=1, col=1,
    )
    if not sp.empty:
        mean_p = float(sp["systemSellPrice"].mean())
        fig.add_hline(y=mean_p, line=dict(color="#7f8c8d", dash="dash",
                                           width=1), row=1, col=1,
                      annotation_text=f"mean £{mean_p:.0f}",
                      annotation_position="right")

    # --- Demand -----------------------------------------------------------
    dem = dem.sort_values("startTime")
    if "initialDemandOutturn" in dem.columns:
        fig.add_trace(
            go.Scatter(
                x=dem["startTime"], y=dem["initialDemandOutturn"] / 1000,
                mode="lines", name="INDO",
                line=dict(color="#2980b9", width=2),
                hovertemplate="%{x|%H:%M}<br>%{y:.1f} GW (INDO)<extra></extra>",
            ),
            row=2, col=1,
        )
    if "initialTransmissionSystemDemandOutturn" in dem.columns:
        fig.add_trace(
            go.Scatter(
                x=dem["startTime"],
                y=dem["initialTransmissionSystemDemandOutturn"] / 1000,
                mode="lines", name="ITSDO",
                line=dict(color="#1abc9c", width=2, dash="dash"),
                hovertemplate="%{x|%H:%M}<br>%{y:.1f} GW (ITSDO)<extra></extra>",
            ),
            row=2, col=1,
        )

    # --- Generation mix (stacked area) -----------------------------------
    pivot = (gen.pivot_table(index="startTime", columns="psrType",
                              values="quantity", aggfunc="sum")
                .fillna(0) / 1000)  # GW
    fuels = [f for f in FUEL_ORDER if f in pivot.columns]
    for fuel in fuels:
        fig.add_trace(
            go.Scatter(
                x=pivot.index, y=pivot[fuel],
                mode="lines", name=fuel, stackgroup="gen",
                line=dict(width=0.3, color=FUEL_COLOURS.get(fuel, "#bdc3c7")),
                fillcolor=FUEL_COLOURS.get(fuel, "#bdc3c7"),
                hovertemplate=f"{fuel}<br>%{{x|%H:%M}}<br>%{{y:.2f}} GW<extra></extra>",
            ),
            row=3, col=1,
        )

    fig.update_yaxes(title_text="£/MWh", row=1, col=1)
    fig.update_yaxes(title_text="GW", row=2, col=1)
    fig.update_yaxes(title_text="GW", row=3, col=1)
    fig.update_xaxes(title_text="Time (UTC)", row=3, col=1,
                     rangeslider=dict(visible=True, thickness=0.04))

    fig.update_layout(
        title=dict(
            text=f"<b>UK Power Markets — {target}</b>"
                 f"<br><sup>Live data: Elexon BMRS Insights Solution</sup>",
            x=0.01, xanchor="left",
        ),
        height=920,
        margin=dict(l=60, r=30, t=90, b=40),
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.06, yanchor="top",
                    x=0.5, xanchor="center", font=dict(size=10)),
        template="plotly_white",
    )
    return fig


def kpi_strip(sp: pd.DataFrame, dem: pd.DataFrame, gen: pd.DataFrame,
              target: date) -> str:
    """Small HTML KPI strip rendered above the plot."""
    sells = sp["systemSellPrice"].dropna()
    p_mean = sells.mean() if len(sells) else float("nan")
    p_peak = sells.max() if len(sells) else float("nan")
    p_trough = sells.min() if len(sells) else float("nan")
    spread = p_peak - p_trough if len(sells) else float("nan")

    if "initialTransmissionSystemDemandOutturn" in dem.columns:
        d_peak = pd.to_numeric(dem["initialTransmissionSystemDemandOutturn"],
                               errors="coerce").max() / 1000
        d_mean = pd.to_numeric(dem["initialTransmissionSystemDemandOutturn"],
                               errors="coerce").mean() / 1000
    else:
        d_peak = d_mean = float("nan")

    total_avg = gen.groupby("startTime")["quantity"].sum().mean() / 1000
    ren_avg = (gen[gen["psrType"].isin(RENEWABLES)]
               .groupby("startTime")["quantity"].sum().mean() / 1000)
    ren_pct = (ren_avg / total_avg * 100) if total_avg else float("nan")

    cards = [
        ("Day mean price", f"£{p_mean:,.0f}/MWh"),
        ("Peak price", f"£{p_peak:,.0f}/MWh"),
        ("Trough price", f"£{p_trough:,.0f}/MWh"),
        ("Intraday spread", f"£{spread:,.0f}/MWh"),
        ("Peak ITSDO", f"{d_peak:,.1f} GW"),
        ("Mean ITSDO", f"{d_mean:,.1f} GW"),
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
<title>UK Power Markets — {target}</title>
<style>
  :root {{
    --bg: #f7f9fc;
    --card: #ffffff;
    --text: #1f2d3d;
    --muted: #6b7c93;
    --accent: #2c3e50;
    --border: #e3e8ee;
  }}
  body {{
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
                 sans-serif;
    background: var(--bg);
    color: var(--text);
  }}
  header {{
    background: var(--accent);
    color: #fff;
    padding: 20px 32px;
  }}
  header h1 {{ margin: 0; font-size: 20px; font-weight: 600; }}
  header .sub {{ font-size: 13px; opacity: 0.75; margin-top: 4px; }}
  main {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
  .kpi-strip {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
    margin-bottom: 20px;
  }}
  .kpi {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
  }}
  .kpi-label {{
    color: var(--muted);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  .kpi-value {{
    font-size: 22px;
    font-weight: 600;
    margin-top: 6px;
  }}
  .chart-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px;
  }}
  footer {{
    text-align: center;
    color: var(--muted);
    font-size: 12px;
    padding: 24px;
  }}
</style>
</head>
<body>
<header>
  <h1>UK Power Markets Dashboard</h1>
  <div class="sub">Settlement date {target} · Source: Elexon BMRS Insights Solution</div>
</header>
<main>
  {kpis}
  <div class="chart-card">{plot}</div>
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
    sp, dem, gen = fetch(target)
    fig = build_figure(sp, dem, gen, target)
    plot_div = fig.to_html(include_plotlyjs="cdn", full_html=False,
                            div_id="dash", config={"displaylogo": False})
    html = PAGE_TEMPLATE.format(
        target=target,
        kpis=kpi_strip(sp, dem, gen, target),
        plot=plot_div,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
