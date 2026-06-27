"""Weather data client using the free Open-Meteo API.

Used to compare day-ahead weather forecasts (wind speed, shortwave
irradiance) against ERA5 reanalysis "outturn" weather. Differences
between forecast and outturn weather largely explain renewable
generation forecast errors.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

import pandas as pd

from ._http import get_json


FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/era5"

# A handful of representative UK wind/solar sites: Hornsea, Dogger Bank,
# Whitelee, a Scottish onshore cluster, and a southern solar hub.
DEFAULT_SITES = {
    "Hornsea (offshore wind)": (53.88, 1.79),
    "Dogger Bank (offshore wind)": (55.00, 1.80),
    "Whitelee (onshore wind)": (55.68, -4.27),
    "London (demand)": (51.50, -0.12),
    "Cornwall (solar)": (50.36, -4.92),
}


def _site_frame(records: list[dict], site: str) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if df.empty:
        return df
    df["site"] = site
    return df


class WeatherClient:
    def __init__(self, *, ttl_seconds: int = 3600):
        self.ttl_seconds = ttl_seconds

    def forecast(
        self,
        sites: dict[str, tuple[float, float]] | None = None,
        *,
        hours: int = 48,
        variables: Iterable[str] = (
            "wind_speed_100m",
            "wind_speed_10m",
            "shortwave_radiation",
            "temperature_2m",
        ),
    ) -> pd.DataFrame:
        sites = sites or DEFAULT_SITES
        frames: list[pd.DataFrame] = []
        for name, (lat, lon) in sites.items():
            payload = get_json(
                FORECAST_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "hourly": ",".join(variables),
                    "forecast_days": max(1, hours // 24),
                    "timezone": "UTC",
                },
                ttl_seconds=self.ttl_seconds,
            )
            hourly = (payload or {}).get("hourly", {})
            if not hourly:
                continue
            records = [
                {"time": t, **{v: hourly.get(v, [None] * len(hourly.get("time", [])))[i]
                                for v in variables}}
                for i, t in enumerate(hourly.get("time", []))
            ]
            frames.append(_site_frame(records, name))
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df["kind"] = "forecast"
        return df

    def outturn(
        self,
        start: date,
        end: date,
        sites: dict[str, tuple[float, float]] | None = None,
        *,
        variables: Iterable[str] = (
            "wind_speed_100m",
            "wind_speed_10m",
            "shortwave_radiation",
            "temperature_2m",
        ),
    ) -> pd.DataFrame:
        """ERA5 reanalysis values for the same variables, ~5 day lag."""
        sites = sites or DEFAULT_SITES
        frames: list[pd.DataFrame] = []
        for name, (lat, lon) in sites.items():
            payload = get_json(
                ARCHIVE_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "hourly": ",".join(variables),
                    "timezone": "UTC",
                },
                ttl_seconds=self.ttl_seconds,
            )
            hourly = (payload or {}).get("hourly", {})
            if not hourly:
                continue
            records = [
                {"time": t, **{v: hourly.get(v, [None] * len(hourly.get("time", [])))[i]
                                for v in variables}}
                for i, t in enumerate(hourly.get("time", []))
            ]
            frames.append(_site_frame(records, name))
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df["kind"] = "outturn"
        return df
