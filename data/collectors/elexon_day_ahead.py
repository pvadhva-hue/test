"""Fetch UK day-ahead electricity prices from the Elexon BMRS Insights API.

Elexon publishes Market Index Data (MID) reported by the two GB power
exchanges, N2EX (Nord Pool) and APX (EPEX). The day-ahead reference price
sits in the Market Index Data feed on the public BMRS Insights API
(https://developer.data.elexon.co.uk/), which needs no API key.

Usage:
    python -m data.collectors.elexon_day_ahead --from 2026-06-20 --to 2026-06-26
    python -m data.collectors.elexon_day_ahead --from 2026-06-20 --to 2026-06-26 \
        --provider N2EX --out da_prices.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Iterator

import requests

BMRS_BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1"
MARKET_INDEX_ENDPOINT = f"{BMRS_BASE_URL}/balancing/pricing/market-index"

# Elexon serves at most 7 days per Market Index request, so longer ranges
# are split into weekly windows.
MAX_WINDOW_DAYS = 7
REQUEST_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class DayAheadPrice:
    settlement_date: str
    settlement_period: int
    start_time: str
    provider: str
    price_gbp_per_mwh: float
    volume_mwh: float | None


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _iter_windows(start: date, end: date) -> Iterator[tuple[date, date]]:
    cursor = start
    step = timedelta(days=MAX_WINDOW_DAYS)
    one_day = timedelta(days=1)
    while cursor <= end:
        window_end = min(cursor + step - one_day, end)
        yield cursor, window_end
        cursor = window_end + one_day


def _to_iso_utc(d: date, end_of_day: bool = False) -> str:
    moment = datetime.combine(
        d, datetime.max.time() if end_of_day else datetime.min.time(), tzinfo=timezone.utc
    )
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_day_ahead_prices(
    start: date,
    end: date,
    provider: str | None = None,
    session: requests.Session | None = None,
) -> list[DayAheadPrice]:
    """Return Market Index day-ahead prices between ``start`` and ``end`` inclusive.

    ``provider`` filters the response to a single exchange (``"N2EX"`` or
    ``"APX MID"``). When omitted, prices from both exchanges are returned.
    """
    if end < start:
        raise ValueError("end date must not be before start date")

    http = session or requests.Session()
    results: list[DayAheadPrice] = []

    for window_start, window_end in _iter_windows(start, end):
        params = {
            "from": _to_iso_utc(window_start),
            "to": _to_iso_utc(window_end, end_of_day=True),
            "format": "json",
        }
        response = http.get(
            MARKET_INDEX_ENDPOINT, params=params, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        payload = response.json()
        results.extend(_parse_records(payload.get("data", []), provider))

    results.sort(key=lambda r: (r.settlement_date, r.settlement_period, r.provider))
    return results


def _parse_records(
    records: Iterable[dict], provider: str | None
) -> Iterator[DayAheadPrice]:
    wanted = provider.strip().upper() if provider else None
    for record in records:
        name = str(record.get("dataProvider") or "").strip()
        if wanted and name.upper() != wanted:
            continue
        try:
            yield DayAheadPrice(
                settlement_date=record["settlementDate"],
                settlement_period=int(record["settlementPeriod"]),
                start_time=record.get("startTime", ""),
                provider=name,
                price_gbp_per_mwh=float(record["price"]),
                volume_mwh=(
                    float(record["volume"]) if record.get("volume") is not None else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            print(f"warning: skipping malformed record {record!r}: {exc}", file=sys.stderr)


def write_csv(rows: list[DayAheadPrice], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "settlement_date",
                "settlement_period",
                "start_time",
                "provider",
                "price_gbp_per_mwh",
                "volume_mwh",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.settlement_date,
                    row.settlement_period,
                    row.start_time,
                    row.provider,
                    f"{row.price_gbp_per_mwh:.4f}",
                    "" if row.volume_mwh is None else f"{row.volume_mwh:.4f}",
                ]
            )


def _build_parser() -> argparse.ArgumentParser:
    today = date.today()
    parser = argparse.ArgumentParser(
        description="Download UK day-ahead Market Index prices from Elexon BMRS."
    )
    parser.add_argument(
        "--from",
        dest="start",
        type=_parse_date,
        default=today - timedelta(days=7),
        help="First settlement date (YYYY-MM-DD). Default: 7 days ago.",
    )
    parser.add_argument(
        "--to",
        dest="end",
        type=_parse_date,
        default=today,
        help="Last settlement date (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--provider",
        choices=["N2EX", "APX MID"],
        help="Filter to a single exchange (default: both).",
    )
    parser.add_argument(
        "--out",
        help="Write results to this CSV path instead of stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    rows = fetch_day_ahead_prices(args.start, args.end, args.provider)
    if not rows:
        print("no day-ahead prices returned for the requested window", file=sys.stderr)
        return 1

    if args.out:
        write_csv(rows, args.out)
        print(f"wrote {len(rows)} rows to {args.out}")
    else:
        writer = csv.writer(sys.stdout)
        writer.writerow(
            [
                "settlement_date",
                "settlement_period",
                "start_time",
                "provider",
                "price_gbp_per_mwh",
                "volume_mwh",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.settlement_date,
                    row.settlement_period,
                    row.start_time,
                    row.provider,
                    f"{row.price_gbp_per_mwh:.4f}",
                    "" if row.volume_mwh is None else f"{row.volume_mwh:.4f}",
                ]
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
