"""Shared HTTP helpers with caching and retries."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import requests


DEFAULT_CACHE_DIR = Path(os.environ.get("POWERDASH_CACHE_DIR", "data/cache"))
DEFAULT_TIMEOUT = 30
DEFAULT_TTL_SECONDS = 60 * 30  # 30 minutes


def _cache_key(url: str, params: dict[str, Any] | None) -> Path:
    payload = json.dumps({"url": url, "params": params or {}}, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_CACHE_DIR / f"{digest}.json"


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    retries: int = 3,
    use_cache: bool = True,
) -> Any:
    """GET a JSON endpoint with disk cache and retry/backoff.

    Cache key is the (url, params) tuple. Stale cache is reused on failure.
    """
    cache_path = _cache_key(url, params) if use_cache else None
    if cache_path and cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < ttl_seconds:
            try:
                return json.loads(cache_path.read_text())
            except json.JSONDecodeError:
                cache_path.unlink(missing_ok=True)

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(
                url,
                params=params,
                timeout=timeout,
                headers={"Accept": "application/json", "User-Agent": "powerdash/0.1"},
            )
            resp.raise_for_status()
            data = resp.json()
            if cache_path:
                cache_path.write_text(json.dumps(data))
            return data
        except (requests.RequestException, ValueError) as exc:  # noqa: PERF203
            last_err = exc
            time.sleep(0.5 * (2**attempt))

    # Fall back to stale cache rather than crashing the dashboard
    if cache_path and cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except json.JSONDecodeError:
            pass
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")
