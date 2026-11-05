"""Shared HTTP layer: one async client, a cache, rate limits, and snapshot fallback.

Owner: B (built first: A and C use it too).

Limits from the plan: Steam store endpoints (appdetails, appreviews) ~200 requests / 5 min;
SteamSpy 1 request / s. SteamCharts has no API and may block cloud IPs.
"""

from pathlib import Path

import httpx

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshots"
USER_AGENT = "SteamReviewDetective/0.1 (IEOR4570 course project)"

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    """The process-wide AsyncClient (async, so parallel researchers really run in parallel)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    return _client


async def get_json(url: str, params: dict | None = None, *, host_limit: str | None = None, cache_key: str | None = None) -> dict:
    """GET url and return its JSON, through the cache and the per-host rate limiter.

    TODO(B): in-memory cache (cache_key -> (time, data)), per-host limiter (asyncio.Semaphore +
    spacing), retries on 429/5xx. Raise on failure so the caller can fall back to a snapshot.
    """
    raise NotImplementedError


async def get_text(url: str, params: dict | None = None, *, host_limit: str | None = None) -> str:
    """GET url and return text (SteamCharts HTML). TODO(B)."""
    raise NotImplementedError


def load_snapshot(appid: int, name: str) -> dict | None:
    """Read data/snapshots/<appid>/<name>.json, or None. Tools fall back to this when the live call fails,
    and set "from_snapshot": True and "as_of" in their result. TODO(B)."""
    raise NotImplementedError


def save_snapshot(appid: int, name: str, data: dict) -> Path:
    """Write data/snapshots/<appid>/<name>.json (used by scripts/make_snapshot.py). TODO(B)."""
    raise NotImplementedError


def error(kind: str, message: str, source: str) -> dict:
    """The shared failure shape every tool returns."""
    return {"ok": False, "error": kind, "message": message, "source": source}


def not_implemented(source: str) -> dict:
    return error("not_implemented", "This tool is a stub.", source)
