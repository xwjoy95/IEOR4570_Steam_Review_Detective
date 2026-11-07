"""Offline shared transport tests; MockTransport never contacts a service."""
import asyncio
import json
import time

import httpx
import pytest

from tools import http


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(http, "HOST_INTERVALS", {"example.com": 0})
    def blocked():
        raise AssertionError("Unexpected live HTTP")
    monkeypatch.setattr(http, "client", blocked)


def test_cache_identity_and_mutation_safety(monkeypatch):
    async def scenario():
        calls = []
        def respond(req):
            calls.append(str(req.url))
            return httpx.Response(200, json={"nested": {"value": len(calls)}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
            monkeypatch.setattr(http, "client", lambda: transport)
            first = await http.get_json("https://example.com/data", {"cc": "US", "cursor": "*"}, cache_key="same")
            first["nested"]["value"] = 99
            assert (await http.get_json("https://example.com/data", {"cursor": "*", "cc": "US"}, cache_key="same"))["nested"]["value"] == 1
            await http.get_json("https://example.com/data", {"cc": "CN", "cursor": "*"}, cache_key="same")
            await http.get_json("https://example.com/data", {"cc": "US", "cursor": "next"}, cache_key="same")
            assert len(calls) == 3
    run(scenario())


def test_cache_ttl_capacity_and_text_json_separation(monkeypatch):
    monkeypatch.setattr(http, "CACHE_SIZE", 2)
    async def scenario():
        calls = []
        def respond(req):
            calls.append(req.url.path)
            return httpx.Response(200, json={"call": len(calls)})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
            monkeypatch.setattr(http, "client", lambda: transport)
            url = "https://example.com/1"
            await http.get_json(url)
            assert json.loads(await http.get_text(url))["call"] == 2
            await http.get_json("https://example.com/2")
            assert len(http._state()["cache"]) == 2
            await http.get_json(url)  # oldest was evicted
            assert len(calls) == 4
            monkeypatch.setattr(http, "CACHE_TTL", 0)
            await http.get_json(url)
            assert len(calls) == 5
    run(scenario())


@pytest.mark.parametrize("failure", [429, 503, "timeout"])
def test_transient_retry(monkeypatch, failure):
    async def scenario():
        calls = []
        def respond(req):
            calls.append(req)
            if len(calls) == 1:
                if failure == "timeout":
                    raise httpx.ReadTimeout("simulated", request=req)
                return httpx.Response(failure, headers={"Retry-After": "0"})
            return httpx.Response(200, json={"ok": True})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
            monkeypatch.setattr(http, "client", lambda: transport)
            assert await http.get_json("https://example.com/data") == {"ok": True}
            assert len(calls) == 2
    run(scenario())


def test_retries_bounded_failures_not_cached(monkeypatch):
    async def scenario():
        calls = []
        def respond(req):
            calls.append(req)
            return httpx.Response(503, headers={"Retry-After": "0"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
            monkeypatch.setattr(http, "client", lambda: transport)
            for _ in range(2):
                with pytest.raises(httpx.HTTPStatusError):
                    await http.get_json("https://example.com/data")
            assert len(calls) == 6
            assert not http._state()["cache"]
    run(scenario())


def test_permanent_errors_and_bad_json_do_not_retry(monkeypatch):
    async def scenario():
        calls = []
        def respond(req):
            calls.append(req)
            return httpx.Response(404 if req.url.path == "/missing" else 200, text="not JSON")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
            monkeypatch.setattr(http, "client", lambda: transport)
            with pytest.raises(httpx.HTTPStatusError):
                await http.get_json("https://example.com/missing")
            with pytest.raises(ValueError):
                await http.get_json("https://example.com/bad")
            assert len(calls) == 2 and not http._state()["cache"]
    run(scenario())


def test_concurrent_same_host_spacing(monkeypatch):
    monkeypatch.setattr(http, "HOST_INTERVALS", {"example.com": 0.025})
    async def scenario():
        times = []
        def respond(req):
            times.append(time.monotonic())
            return httpx.Response(200, json={})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
            monkeypatch.setattr(http, "client", lambda: transport)
            await asyncio.gather(*(http.get_json(f"https://example.com/{i}") for i in range(3)))
        assert all(b - a >= 0.020 for a, b in zip(times, times[1:]))
    run(scenario())


def test_long_retry_after_and_overall_timeout(monkeypatch):
    async def scenario():
        async def respond(req):
            if req.url.path == "/slow":
                await asyncio.sleep(1)
            return httpx.Response(429, headers={"Retry-After": "999"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
            monkeypatch.setattr(http, "client", lambda: transport)
            with pytest.raises(httpx.HTTPStatusError):
                await http.get_json("https://example.com/backoff")
            monkeypatch.setattr(http, "REQUEST_BUDGET", 0.01)
            with pytest.raises(asyncio.TimeoutError):
                await http.get_json("https://another.example/slow")
    run(scenario())


def test_snapshots_roundtrip_corruption_and_credentials(tmp_path):
    data = {"params": {"appid": 42}, "data": {"name": "\u6d4b\u8bd5"}, "as_of": "2026-01-01"}
    path = http.save_snapshot(42, "appdetails", data)
    assert "captured_at" not in data
    assert http.load_snapshot(42, "appdetails")["as_of"] == "2026-01-01"
    assert http.load_snapshot(42, "appdetails")["data"] == data["data"]
    assert http.load_snapshot(42, "missing") is None
    assert not list(path.parent.glob("*.tmp"))
    path.write_text("broken JSON", encoding="utf-8")
    assert http.load_snapshot(42, "appdetails") is None
    for name, value in [("../escape", {}), ("private", {"params": {"key": "test-only-placeholder"}})]:
        with pytest.raises(ValueError):
            http.save_snapshot(42, name, value)
    for appid in [0, -1, True]:
        with pytest.raises(ValueError):
            http.save_snapshot(appid, "test", {})
    assert not list(tmp_path.glob("*/private.json"))


def test_failed_write_preserves_previous_snapshot(monkeypatch):
    http.save_snapshot(42, "test", {"data": "old"})
    def fail(*args):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(type(http.SNAPSHOT_DIR), "replace", fail)
    with pytest.raises(OSError):
        http.save_snapshot(42, "test", {"data": "new"})
    assert http.load_snapshot(42, "test")["data"] == "old"
    assert not list(http.SNAPSHOT_DIR.rglob("*.tmp"))
