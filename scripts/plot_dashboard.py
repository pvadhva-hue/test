"""Render a single PNG dashboard: system prices, demand, generation mix.

Usage:
    python -m scripts.plot_dashboard [--date YYYY-MM-DD] [--out file.png]

Defaults: yesterday UTC, writes to data/cache/dashboard.png.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from powerdash.collectors import ElexonClient


# Consistent colour scheme for fuels (carbonintensity.org.uk style)
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


def fetch(target: date) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    el = ElexonClient()
    sp = el.system_prices(target)
    dem = el.demand_outturn(target)
    start = datetime(target.year, target.month, target.day, tzinfo=timezone.utc)
    gen = el.generation_by_fuel(start, start + timedelta(days=1))
    return sp, dem, gen


def plot(sp: pd.DataFrame, dem: pd.DataFrame, gen: pd.DataFrame,
         target: date, out_path: Path) -> Path:
    fig, axes = plt.subplots(3, 1, figsize=(14, 12), sharex=True,
                              gridspec_kw={"height_ratios": [1, 1, 1.4]})
    fig.suptitle(f"UK Power Markets — {target} (Elexon BMRS)",
                 fontsize=15, fontweight="bold", y=0.995)

    # ---- System prices ----------------------------------------------------
    ax = axes[0]
    sp = sp.sort_values("startTime")
    ax.plot(sp["startTime"], sp["systemSellPrice"],
            color="#c0392b", linewidth=1.8, label="System price (£/MWh)")
    ax.fill_between(sp["startTime"], sp["systemSellPrice"], alpha=0.15,
                    color="#c0392b")
    mean_p = sp["systemSellPrice"].mean()
    ax.axhline(mean_p, color="#7f8c8d", linestyle="--", linewidth=0.8,
               label=f"Day mean £{mean_p:.0f}")
    peak = sp.loc[sp["systemSellPrice"].idxmax()]
    trough = sp.loc[sp["systemSellPrice"].idxmin()]
    ax.annotate(f"peak £{peak['systemSellPrice']:.0f}",
                (peak["startTime"], peak["systemSellPrice"]),
                textcoords="offset points", xytext=(0, 8),
                ha="center", fontsize=9, color="#c0392b")
    ax.annotate(f"low £{trough['systemSellPrice']:.0f}",
                (trough["startTime"], trough["systemSellPrice"]),
                textcoords="offset points", xytext=(0, -14),
                ha="center", fontsize=9, color="#16a085")
    ax.set_ylabel("£/MWh")
    ax.set_title("System imbalance price", loc="left", fontsize=11)
    ax.legend(loc="upper left", fontsize=9, framealpha=0.9)
    ax.grid(True, alpha=0.3)

    # ---- Demand -----------------------------------------------------------
    ax = axes[1]
    dem = dem.sort_values("startTime")
    if "initialDemandOutturn" in dem.columns:
        ax.plot(dem["startTime"], dem["initialDemandOutturn"] / 1000,
                color="#2980b9", linewidth=1.8, label="INDO")
    if "initialTransmissionSystemDemandOutturn" in dem.columns:
        ax.plot(dem["startTime"],
                dem["initialTransmissionSystemDemandOutturn"] / 1000,
                color="#1abc9c", linewidth=1.6, linestyle="--", label="ITSDO")
    ax.set_ylabel("GW")
    ax.set_title("Demand outturn", loc="left", fontsize=11)
    ax.legend(loc="upper left", fontsize=9, framealpha=0.9)
    ax.grid(True, alpha=0.3)

    # ---- Generation by fuel (stacked area) -------------------------------
    ax = axes[2]
    pivot = (gen.pivot_table(index="startTime", columns="psrType",
                              values="quantity", aggfunc="sum")
                .fillna(0) / 1000)  # GW
    fuels = [f for f in FUEL_ORDER if f in pivot.columns]
    colours = [FUEL_COLOURS.get(f, "#bdc3c7") for f in fuels]
    ax.stackplot(pivot.index, pivot[fuels].T.values,
                 labels=fuels, colors=colours, alpha=0.9)
    ax.set_ylabel("GW")
    ax.set_title("Generation mix by fuel", loc="left", fontsize=11)
    ax.legend(loc="upper left", fontsize=8, ncol=3, framealpha=0.9)
    ax.grid(True, alpha=0.3)

    # Shared x-axis formatting
    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=3))
    axes[-1].set_xlabel("Time (UTC)")

    fig.tight_layout(rect=(0, 0, 1, 0.99))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None,
                    help="Settlement date YYYY-MM-DD (default: yesterday UTC)")
    ap.add_argument("--out", default="data/cache/dashboard.png",
                    help="Output PNG path")
    args = ap.parse_args()
    target = (date.fromisoformat(args.date) if args.date
              else (datetime.now(timezone.utc) - timedelta(days=1)).date())
    sp, dem, gen = fetch(target)
    out = plot(sp, dem, gen, target, Path(args.out))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
