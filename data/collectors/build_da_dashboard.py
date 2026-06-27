"""Render a self-contained HTML dashboard from a day-ahead price CSV.

The CSV is the output of ``elexon_day_ahead.py``. The HTML embeds the data
inline and loads Chart.js from a CDN so the file is portable.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from html import escape
from pathlib import Path
from statistics import mean
from typing import Iterable

PROVIDER_LABELS = {
    "APXMIDP": "APX (EPEX)",
    "N2EXMIDP": "N2EX (Nord Pool)",
}


def load_rows(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _provider_label(code: str) -> str:
    return PROVIDER_LABELS.get(code, code)


def _format_window(rows: list[dict]) -> str:
    dates = sorted({r["settlement_date"] for r in rows})
    if not dates:
        return ""
    if len(dates) == 1:
        return dates[0]
    return f"{dates[0]} → {dates[-1]}"


def build_series(rows: list[dict]) -> dict:
    """Group rows into per-provider time series keyed by start_time."""
    series: dict[str, dict[str, float]] = defaultdict(dict)
    timestamps: set[str] = set()
    for row in rows:
        provider = row["provider"]
        ts = row["start_time"]
        try:
            price = float(row["price_gbp_per_mwh"])
        except ValueError:
            continue
        series[provider][ts] = price
        timestamps.add(ts)

    ordered_ts = sorted(timestamps)
    return {
        "timestamps": ordered_ts,
        "providers": {
            provider: [series[provider].get(ts) for ts in ordered_ts]
            for provider in sorted(series)
        },
    }


def daily_summary(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    peak_grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        try:
            price = float(row["price_gbp_per_mwh"])
            period = int(row["settlement_period"])
        except ValueError:
            continue
        key = (row["settlement_date"], row["provider"])
        grouped[key].append(price)
        # Peak = settlement periods 15-38 (07:00-19:00 local-equivalent half hours)
        if 15 <= period <= 38:
            peak_grouped[key].append(price)

    summary = []
    for (day, provider), prices in sorted(grouped.items()):
        nonzero = [p for p in prices if p != 0]
        baseload = mean(prices) if prices else 0.0
        peak = mean(peak_grouped[(day, provider)]) if peak_grouped[(day, provider)] else 0.0
        summary.append(
            {
                "date": day,
                "provider": _provider_label(provider),
                "baseload": baseload,
                "peak": peak,
                "min": min(prices) if prices else 0.0,
                "max": max(prices) if prices else 0.0,
                "n": len(prices),
                "nonzero": len(nonzero),
            }
        )
    return summary


def _format_timestamps(timestamps: Iterable[str]) -> list[str]:
    out = []
    for ts in timestamps:
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            out.append(parsed.strftime("%a %d %b %H:%M"))
        except ValueError:
            out.append(ts)
    return out


PALETTE = ["#2563eb", "#dc2626", "#059669", "#7c3aed", "#ea580c"]


def render_html(rows: list[dict]) -> str:
    series = build_series(rows)
    summary = daily_summary(rows)
    window = _format_window(rows)
    labels = _format_timestamps(series["timestamps"])

    datasets = []
    for idx, (provider, values) in enumerate(series["providers"].items()):
        datasets.append(
            {
                "label": _provider_label(provider),
                "data": values,
                "borderColor": PALETTE[idx % len(PALETTE)],
                "backgroundColor": PALETTE[idx % len(PALETTE)] + "33",
                "tension": 0.25,
                "spanGaps": True,
                "pointRadius": 0,
                "borderWidth": 2,
            }
        )

    summary_rows = "\n".join(
        f"<tr><td>{escape(r['date'])}</td>"
        f"<td>{escape(r['provider'])}</td>"
        f"<td class='num'>£{r['baseload']:.2f}</td>"
        f"<td class='num'>£{r['peak']:.2f}</td>"
        f"<td class='num'>£{r['min']:.2f}</td>"
        f"<td class='num'>£{r['max']:.2f}</td>"
        f"<td class='num'>{r['nonzero']}/{r['n']}</td></tr>"
        for r in summary
    )

    chart_payload = {"labels": labels, "datasets": datasets}

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>UK Day-Ahead Prices — {escape(window)}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0; padding: 24px; background: #f8fafc; color: #0f172a; }}
  h1 {{ margin: 0 0 4px; font-size: 22px; }}
  .sub {{ color: #64748b; margin-bottom: 24px; font-size: 14px; }}
  .card {{ background: white; border-radius: 12px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 24px; }}
  .chart-wrap {{ position: relative; height: 420px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }}
  th {{ background: #f1f5f9; font-weight: 600; color: #475569; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  tr:hover {{ background: #f8fafc; }}
  footer {{ color: #94a3b8; font-size: 12px; margin-top: 16px; }}
</style>
</head>
<body>
<h1>UK Day-Ahead Prices</h1>
<div class="sub">Market Index Data from Elexon BMRS · {escape(window)} · {len(rows)} half-hourly observations</div>

<div class="card">
  <div class="chart-wrap"><canvas id="priceChart"></canvas></div>
</div>

<div class="card">
  <h2 style="margin-top:0;font-size:16px;">Daily summary (£/MWh)</h2>
  <table>
    <thead><tr><th>Date</th><th>Provider</th><th class='num'>Baseload</th><th class='num'>Peak</th><th class='num'>Min</th><th class='num'>Max</th><th class='num'>Non-zero / Total</th></tr></thead>
    <tbody>
{summary_rows}
    </tbody>
  </table>
  <footer>Peak = settlement periods 15–38 (07:00–19:00 UTC). N2EX rows showing 0.00 indicate the exchange did not publish a Market Index value for that period.</footer>
</div>

<script>
const payload = {json.dumps(chart_payload)};
new Chart(document.getElementById('priceChart'), {{
  type: 'line',
  data: payload,
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{
      title: {{ display: true, text: 'Half-hourly day-ahead price (£/MWh)' }},
      legend: {{ position: 'bottom' }},
      tooltip: {{ callbacks: {{ label: (ctx) => `${{ctx.dataset.label}}: £${{ctx.parsed.y?.toFixed(2) ?? '—'}}` }} }}
    }},
    scales: {{
      x: {{ ticks: {{ maxRotation: 0, autoSkip: true, maxTicksLimit: 14 }} }},
      y: {{ title: {{ display: true, text: '£/MWh' }} }}
    }}
  }}
}});
</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="CSV produced by elexon_day_ahead.py")
    parser.add_argument("--out", type=Path, required=True, help="Output HTML path")
    args = parser.parse_args(argv)

    rows = load_rows(args.csv)
    if not rows:
        print("input CSV is empty", flush=True)
        return 1

    args.out.write_text(render_html(rows), encoding="utf-8")
    print(f"wrote {args.out} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
