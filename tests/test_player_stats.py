"""Offline tests for C's player tools, on real responses saved in tests/fixtures/player_stats/.
No network: tools.http.get_json / get_text / load_snapshot are replaced per test."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from agents.tool_context import ToolContext

from detective.hooks import TraceHooks
from detective.state import Research
from tools import http
from tools import player_stats as ps

FIXTURES = Path(__file__).parent / "fixtures" / "player_stats"
BG3 = 1086940


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def call(tool, state: Research, **args) -> dict:
    ctx = ToolContext(context=state, tool_name=tool.name, tool_call_id="c1", tool_arguments=json.dumps(args))
    out = asyncio.run(tool.on_invoke_tool(ctx, json.dumps(args)))
    return out if isinstance(out, dict) else json.loads(out)


@pytest.fixture
def state():
    return Research(hooks=TraceHooks())


@pytest.fixture
def live(monkeypatch):
    """Serve the saved fixtures instead of the network. Returns the dict of responses to tweak."""
    responses = {
        ps.STEAMSPY_URL: json.loads(fixture("steamspy_1086940.json")),
        ps.CURRENT_PLAYERS_URL: json.loads(fixture("current_1086940.json")),
        ps.STEAMCHARTS_URL.format(appid=BG3): fixture("steamcharts_1086940.html"),
    }

    async def get(url, params=None, **kwargs):
        value = responses.get(url)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise httpx.HTTPStatusError("404", request=httpx.Request("GET", url), response=httpx.Response(404))
        return value

    monkeypatch.setattr(http, "get_json", get)
    monkeypatch.setattr(http, "get_text", get)
    monkeypatch.setattr(http, "load_snapshot", lambda appid, name: None)
    return responses


# --- Parsers ---


def test_parse_steamspy_real_sample():
    stats = ps.parse_steamspy(json.loads(fixture("steamspy_1086940.json")), BG3)
    assert stats["name"] == "Baldur's Gate 3"
    assert stats["owners_estimate"] == "20,000,000 .. 50,000,000"
    assert stats["average_forever_h"] is None  # SteamSpy currently reports 0 minutes: no data, not 0 h
    assert stats["ccu_yesterday"] == 54771
    assert len(stats["tags"]) == 10 and next(iter(stats["tags"])) == "RPG"


def test_parse_steamspy_minutes_to_hours_and_unknown_app():
    raw = json.loads(fixture("steamspy_1086940.json")) | {"average_forever": 3684, "median_2weeks": 0}
    stats = ps.parse_steamspy(raw, BG3)
    assert stats["average_forever_h"] == 61.4 and stats["median_2weeks_h"] is None
    with pytest.raises(ps.NoData):
        ps.parse_steamspy({"appid": 999999999, "name": None, "owners": "0 .. 20,000"}, 999999999)


def test_parse_steamcharts_real_samples():
    months = ps.parse_steamcharts(fixture("steamcharts_1086940.html"))
    assert months[0] == {"month": "2020-10", "avg": 16792, "peak": 48418, "gain": None, "gain_pct": None}
    oct26 = next(m for m in months if m["month"] == "2026-10")
    assert oct26 == {"month": "2026-10", "avg": 38530, "peak": 71090, "gain": -4402, "gain_pct": -10.25}
    assert next(m for m in months if m["month"] == "2026-08")["gain_pct"] == 11.53  # "&#43;11.53%"
    assert [m["month"] for m in months] == sorted(m["month"] for m in months)
    assert len(ps.parse_steamcharts(fixture("steamcharts_1172470.html"))) > 60
    with pytest.raises(ValueError):
        ps.parse_steamcharts("<html><body>no table</body></html>")


# --- get_player_stats ---


def test_player_stats_live(live, state):
    out = call(ps.get_player_stats, state, appid=BG3)
    assert out["ok"] and not out["from_snapshot"]
    assert out["current_players"] == 35685 and out["owners_estimate"] == "20,000,000 .. 50,000,000"
    assert state.store.get(out["ref"]).source == "player_stats"


def test_player_stats_without_current_players(live, state):
    live[ps.CURRENT_PLAYERS_URL] = {"response": {"result": 42}}
    out = call(ps.get_player_stats, state, appid=BG3)
    assert out["ok"] and out["current_players"] is None and out["warnings"]


def test_player_stats_falls_back_to_snapshot(live, state, monkeypatch):
    live[ps.STEAMSPY_URL] = httpx.ConnectError("down")
    snap = ps._envelope(BG3, ps.STEAMSPY_URL, {}, json.loads(fixture("steamspy_1086940.json"))) | {"as_of": "2026-11-01"}
    monkeypatch.setattr(http, "load_snapshot", lambda appid, name: snap if name == "steamspy" else None)
    out = call(ps.get_player_stats, state, appid=BG3)
    assert out["ok"] and out["from_snapshot"] and out["as_of"] == "2026-11-01"


def test_player_stats_errors(live, state):
    live[ps.STEAMSPY_URL] = {"appid": BG3, "name": None}
    assert call(ps.get_player_stats, state, appid=BG3)["error"] == "no_data"
    live[ps.STEAMSPY_URL] = httpx.ConnectError("down")
    assert call(ps.get_player_stats, state, appid=BG3)["error"] == "unavailable"
    assert call(ps.get_player_stats, state, appid=-1)["error"] == "invalid_argument"
    assert len(state.store) == 0


# --- get_player_history ---


def test_player_history_filters_months(live, state):
    out = call(ps.get_player_history, state, appid=BG3, start="2026-08-15", end="2026-10-01")
    assert out["ok"] and [m["month"] for m in out["months"]] == ["2026-08", "2026-09", "2026-10"]
    assert out["coverage"][0] == "2020-10"
    assert all(state.store.get(m["ref"]).source == "player_history" for m in out["months"])


def test_player_history_falls_back_to_snapshot(live, state, monkeypatch):
    url = ps.STEAMCHARTS_URL.format(appid=BG3)
    snap = ps._envelope(BG3, url, {"appid": BG3}, {"months": ps.parse_steamcharts(fixture("steamcharts_1086940.html"))})
    live[url] = httpx.HTTPStatusError("403", request=httpx.Request("GET", url), response=httpx.Response(403))
    monkeypatch.setattr(http, "load_snapshot", lambda appid, name: snap if name == "steamcharts" else None)
    out = call(ps.get_player_history, state, appid=BG3, start="2026-09-01", end="2026-09-30")
    assert out["ok"] and out["from_snapshot"] and out["months"][0]["avg"] == 42932


def test_player_history_errors(live, state):
    assert call(ps.get_player_history, state, appid=BG3, start="2026-10", end="2026-11-01")["error"] == "invalid_argument"
    assert call(ps.get_player_history, state, appid=BG3, start="2026-11-01", end="2026-10-01")["error"] == "invalid_argument"
    assert call(ps.get_player_history, state, appid=BG3, start="2019-01-01", end="2019-12-31")["error"] == "no_data"
    assert call(ps.get_player_history, state, appid=2956320, start="2026-01-01", end="2026-10-31")["error"] == "unavailable"
    assert len(state.store) == 0


def test_tools_never_write_snapshots(live, state, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("tools must not write snapshots")

    monkeypatch.setattr(http, "save_snapshot", fail)
    call(ps.get_player_stats, state, appid=BG3)
    call(ps.get_player_history, state, appid=BG3, start="2026-01-01", end="2026-10-31")
