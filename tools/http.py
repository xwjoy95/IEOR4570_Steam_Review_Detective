"""Shared asynchronous HTTP, bounded cache/retries, and atomic local snapshots.

Tools choose their own fallback: this layer never silently substitutes stale data.
State is local to an event loop, allowing both ASGI and repeated asyncio.run calls.
"""

import asyncio
import copy
import json
import math
import re
import tempfile
import time
import weakref
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshots"
USER_AGENT = "SteamReviewDetective/0.1 (IEOR4570 course project)"
CACHE_TTL = 300.0
CACHE_SIZE = 256
REQUEST_BUDGET = 35.0
MAX_ATTEMPTS = 3
HOST_INTERVALS = {"store.steampowered.com": 1.5, "steamspy.com": 1.0,
                  "api.steampowered.com": 0.25, "steamcharts.com": 1.0}
_states = weakref.WeakKeyDictionary()
_SECRET_KEYS = {"key", "api_key", "apikey", "token", "access_token", "authorization", "password", "secret"}


def _state() -> dict:
    loop = asyncio.get_running_loop()
    if loop not in _states:
        _states[loop] = {"client": None, "cache": OrderedDict(), "locks": {}, "next": {}}
    return _states[loop]


def client() -> httpx.AsyncClient:
    state = _state()
    if state["client"] is None or state["client"].is_closed:
        state["client"] = httpx.AsyncClient(timeout=8, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    return state["client"]


async def close_client() -> None:
    """Close this loop's connections (scripts should call this in a finally block)."""
    state = _states.pop(asyncio.get_running_loop(), None)
    if state and state["client"] is not None:
        await state["client"].aclose()


async def _pace(host: str) -> None:
    state = _state()
    lock = state["locks"].setdefault(host, asyncio.Lock())
    async with lock:
        while (delay := state["next"].get(host, 0) - time.monotonic()) > 0:
            await asyncio.sleep(delay)
        state["next"][host] = time.monotonic() + HOST_INTERVALS.get(host, 0.25)


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    value = response.headers.get("Retry-After", "")
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            delay = 0.5 * 2 ** attempt
    return max(0, delay) if math.isfinite(delay) else REQUEST_BUDGET


async def _request(url: str, params: dict | None, host: str) -> httpx.Response:
    for attempt in range(MAX_ATTEMPTS):
        await _pace(host)
        try:
            response = await client().get(url, params=params)
        except httpx.TransportError:
            if attempt == MAX_ATTEMPTS - 1:
                raise
            await asyncio.sleep(0.5 * 2 ** attempt)
            continue
        if response.status_code in {429, 500, 502, 503, 504} and attempt < MAX_ATTEMPTS - 1:
            delay = _retry_delay(response, attempt)
            state = _state()
            state["next"][host] = max(state["next"].get(host, 0), time.monotonic() + delay)
            if delay >= REQUEST_BUDGET:
                response.raise_for_status()
            continue
        response.raise_for_status()
        return response
    raise RuntimeError("Request attempts exhausted")


async def _get(url: str, params: dict | None, host_limit: str | None, kind: str, cache_key: str | None = None):
    # The optional label supplements rather than replaces the request identity.
    key = (kind, url, str(httpx.QueryParams(sorted((params or {}).items()))), cache_key)
    cache = _state()["cache"]
    entry = cache.get(key)
    if entry and time.monotonic() - entry[0] < CACHE_TTL:
        cache.move_to_end(key)
        return copy.deepcopy(entry[1])
    host = urlsplit(url).hostname or "unknown"
    host = {"steam_store": "store.steampowered.com", "steamspy": "steamspy.com",
            "steam_api": "api.steampowered.com", "steamcharts": "steamcharts.com"}.get(host_limit, host_limit) or host
    response = await asyncio.wait_for(_request(url, params, host), timeout=REQUEST_BUDGET)
    value = response.json() if kind == "json" else response.text
    cache[key] = (time.monotonic(), copy.deepcopy(value))
    cache.move_to_end(key)
    while len(cache) > CACHE_SIZE:
        cache.popitem(last=False)
    return value


async def get_json(url: str, params: dict | None = None, *, host_limit: str | None = None, cache_key: str | None = None) -> dict:
    return await _get(url, params, host_limit, "json", cache_key)


async def get_text(url: str, params: dict | None = None, *, host_limit: str | None = None) -> str:
    return await _get(url, params, host_limit, "text")


def _snapshot_path(appid: int, name: str) -> Path:
    if type(appid) is not int or not 0 < appid <= 2**32 - 1:
        raise ValueError("appid must be a positive uint32")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("Invalid snapshot name")
    path = (SNAPSHOT_DIR / str(appid) / f"{name}.json").resolve()
    if not path.is_relative_to(SNAPSHOT_DIR.resolve()):
        raise ValueError("Snapshot path outside snapshot directory")
    return path


def _check_public(value) -> None:
    """Snapshots are shareable data; reject credential fields, including nested ones."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _SECRET_KEYS:
                raise ValueError("Credential fields cannot be stored in data snapshots")
            _check_public(item)
    elif isinstance(value, list):
        for item in value:
            _check_public(item)


def load_snapshot(appid: int, name: str) -> dict | None:
    path = _snapshot_path(appid, name)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None
        _check_public(value)
        return value
    except (OSError, ValueError, UnicodeError):
        return None


def save_snapshot(appid: int, name: str, data: dict) -> Path:
    """Atomically save a public-data dict; keep the caller's capture metadata."""
    path = _snapshot_path(appid, name)
    _check_public(data)
    value = copy.deepcopy(data)
    now = datetime.now(timezone.utc)
    value.setdefault("as_of", now.date().isoformat())
    value.setdefault("captured_at", now.isoformat())
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as out:
            tmp = Path(out.name)
            out.write(encoded)
        tmp.replace(path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return path


def error(kind: str, message: str, source: str) -> dict:
    return {"ok": False, "error": kind, "message": message, "source": source}


def not_implemented(source: str) -> dict:
    return error("not_implemented", "This tool is a stub.", source)
