"""Offline tests for Steam search, store details, and announcements.

Real responses captured on 2026-11-06 are in tests/fixtures/steam_news/.
Mutations below are synthetic edge cases, not real Steam observations.
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from agents.tool_context import ToolContext

from evidence import EvidenceStore
from tools import http, steam_news as news

FIXTURES = Path(__file__).parent / "fixtures" / "steam_news"
APPID = 1086940
START, END = "2026-03-01", "2026-03-31"


def sample(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def state():
    return SimpleNamespace(store=EvidenceStore())


async def invoke(tool, research=None, **args):
    research = research or state()
    ctx = ToolContext(context=research, tool_name=tool.name, tool_call_id="offline", tool_arguments=json.dumps(args))
    result = await tool.on_invoke_tool(ctx, json.dumps(args))
    return json.loads(result) if isinstance(result, str) else result


def call(tool, research=None, **args):
    return asyncio.run(invoke(tool, research, **args))


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "SNAPSHOT_DIR", tmp_path)
    async def blocked(*args, **kwargs):
        raise httpx.ConnectError("simulated offline")
    monkeypatch.setattr(http, "get_json", blocked)


def details_snap(data=None):
    return news._envelope(APPID, news.DETAILS_URL,
                          {"appids": APPID, "cc": "US", "l": "english"}, data or sample("appdetails_1086940"))


def news_row(gid="1", day="2026-03-15", feed=news.OFFICIAL_FEED, **kwargs):
    return {"gid": gid, "date": int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()),
            "title": "Hotfix 1.2", "contents": "[p]We did not change the price.[/p]", "feedname": feed,
            "url": f"https://store.steampowered.com/news/app/{APPID}/view/{gid}", **kwargs}


def news_snap(rows, official=True, complete=True, start=START, end=END):
    return news._envelope(APPID, news.NEWS_URL, {"appid": APPID, "official_only": official},
                          {"appnews": {"appid": APPID, "newsitems": rows}},
                          coverage={"start": start, "end": end, "complete": complete, "reason": "exhausted"})


def test_search_verifies_real_app_type_and_deduplicates(monkeypatch):
    raw = sample("search_bg3")
    raw["items"] += raw["items"][:1]
    async def fake(url, params):
        if url == news.SEARCH_URL:
            return raw
        appid = params["appids"]
        doc = sample("appdetails_1086940")
        data = doc[str(APPID)]["data"]
        data["steam_appid"] = appid
        data["type"] = {APPID: "game", 2378500: "dlc", 2956320: "tool"}[appid]
        return {str(appid): doc[str(APPID)]}
    monkeypatch.setattr(http, "get_json", fake)
    out = call(news.search_game, query="  Baldur's Gate 3  ", limit=5)
    assert out["ok"] and [r["appid"] for r in out["results"]] == [APPID]
    assert out["results"][0]["release_date"] == "2023-08-03"
    assert not out["partial"] and not out["from_snapshot"]


def test_search_ambiguity_keeps_two_games(monkeypatch):
    async def fake(url, params):
        if url == news.SEARCH_URL:
            return {"items": [{"id": APPID}, {"id": APPID + 1}]}
        appid = params["appids"]
        doc = sample("appdetails_1086940")[str(APPID)]
        doc["data"]["steam_appid"] = appid
        doc["data"]["name"] = f"Example {appid}"
        return {str(appid): doc}
    monkeypatch.setattr(http, "get_json", fake)
    out = call(news.search_game, query="Example", limit=5)
    assert len(out["results"]) == 2


def test_search_empty_success_differs_from_network_failure(monkeypatch):
    assert not call(news.search_game, query="No local games", limit=5)["ok"]
    async def empty(*args, **kwargs):
        return sample("search_empty")
    monkeypatch.setattr(http, "get_json", empty)
    out = call(news.search_game, query="Missing", limit=5)
    assert out["ok"] and out["results"] == [] and not out["from_snapshot"]


def test_search_offline_scope_and_candidate_verification_failure(monkeypatch):
    snap = details_snap()
    snap["as_of"] = "2026-02-01"
    http.save_snapshot(APPID, "appdetails", snap)
    out = call(news.search_game, query="Baldur", limit=5)
    assert out["from_snapshot"] and out["partial"] and out["as_of"] == "2026-02-01"
    async def search_only(url, params):
        if url == news.SEARCH_URL:
            return {"items": [{"id": 42}]}
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(http, "get_json", search_only)
    out = call(news.search_game, query="Example", limit=5)
    assert not out["ok"] and out["error"] == "unavailable"


@pytest.mark.parametrize("query,limit", [("", 5), ("  ", 5), ("Game", 0), ("Game", 11)])
def test_search_invalid(query, limit):
    assert call(news.search_game, query=query, limit=limit)["error"] == "invalid_arguments"


def test_details_captured_facts_and_full_evidence(monkeypatch):
    async def fake(url, params):
        assert params == {"appids": APPID, "cc": "US", "l": "english"}
        return sample("appdetails_1086940")
    monkeypatch.setattr(http, "get_json", fake)
    research = state()
    out = call(news.get_game_info, research, appid=APPID)
    assert out["ok"] and out["price"] == "$59.99" and out["currency"] == "USD"
    assert out["region"] == "US" and out["release_date"] == "2023-08-03"
    evidence = research.store.get(out["ref"])
    assert evidence.url == out["url"] and evidence.date == out["as_of"]
    assert '"price_minor": 5999' in evidence.text and "US" in evidence.text
    assert http.load_snapshot(APPID, "appdetails") is None


@pytest.mark.parametrize("free", [True, False])
def test_free_vs_missing_price_and_release_precision(monkeypatch, free):
    doc = sample("appdetails_1086940")
    data = doc[str(APPID)]["data"]
    data.pop("price_overview", None)
    data.pop("metacritic", None)
    data["is_free"] = free
    data["release_date"] = {"date": "Coming soon", "coming_soon": True}
    async def fake(*args, **kwargs):
        return doc
    monkeypatch.setattr(http, "get_json", fake)
    out = call(news.get_game_info, appid=APPID)
    assert out["price"] == ("Free" if free else None)
    assert out["price_minor"] == (0 if free else None)
    assert out["metacritic"] is None and out["release_date"] == "Coming soon"


def test_details_offline_fresh_refs_and_capture_date():
    snap = details_snap()
    snap["as_of"] = "2026-02-01"
    http.save_snapshot(APPID, "appdetails", snap)
    for i in range(2):
        research = state()
        research.store.add("other", "x", "", "", "")
        out = call(news.get_game_info, research, appid=APPID)
        assert out["ok"] and out["from_snapshot"] and out["as_of"] == "2026-02-01"
        assert research.store.get(out["ref"]).raw_id == str(APPID)


def test_details_failure_and_region_mismatch(monkeypatch):
    snap = details_snap()
    snap["params"]["cc"] = "CN"
    http.save_snapshot(APPID, "appdetails", snap)
    assert call(news.get_game_info, appid=APPID)["error"] == "unavailable"
    async def unavailable(*args, **kwargs):
        return {str(APPID): {"success": False}}
    monkeypatch.setattr(http, "get_json", unavailable)
    assert call(news.get_game_info, appid=APPID)["error"] == "not_found"
    assert call(news.get_game_info, appid=-1)["error"] == "invalid_arguments"


def test_markup_preserves_meaning_lists_and_links():
    raw = '[p][b]1.4[/b] did NOT delete saves.[/p][list][*]Fix A[*][url=https://example.com]Read more[/url][/list]<p>Second &amp; third</p>[img]image.png[/img]<script>hidden()</script>'
    cleaned = news.clean_markup(raw)
    assert "1.4 did NOT delete saves." in cleaned and "- Fix A" in cleaned
    assert "Read more" in cleaned and "Second & third" in cleaned
    assert "image.png" not in cleaned and "hidden" not in cleaned and "[" not in cleaned


def test_captured_news_extract_and_source(monkeypatch):
    rows = sample("news_1086940")["appnews"]["newsitems"]
    async def fake(url, params):
        assert params["feeds"] == news.OFFICIAL_FEED
        return {"appnews": {"appid": APPID, "newsitems": rows}}
    monkeypatch.setattr(http, "get_json", fake)
    research = state()
    out = call(news.get_patch_notes, research, appid=APPID, start=START, end=END, official_only=True)
    assert out["ok"] and out["complete"] and len(out["notes"]) == 1
    item = out["notes"][0]
    assert item["title"] == "Hotfix #36 Now Live!" and item["kind"] == "patch_notes"
    assert research.store.get(item["ref"]).raw_id == "1827626365770693"
    assert "[p]" not in item["summary"] and item["summary_is_excerpt"]


def test_same_day_inclusive_filter_and_official_media(monkeypatch):
    rows = [news_row("start", "2026-03-15T00:00:00"), news_row("end", "2026-03-15T23:59:59"),
            news_row("next", "2026-03-16"), news_row("older", "2026-03-14"),
            news_row("press", feed="pcgamer")]
    async def fake(url, params):
        return {"appnews": {"newsitems": rows}}
    monkeypatch.setattr(http, "get_json", fake)
    out = call(news.get_patch_notes, appid=APPID, start="2026-03-15", end="2026-03-15", official_only=True)
    assert {r["id"] for r in out["notes"]} == {"start", "end"}
    out = call(news.get_patch_notes, appid=APPID, start="2026-03-15", end="2026-03-15", official_only=False)
    assert len(out["notes"]) == 3 and any(not r["official"] for r in out["notes"])


def test_pagination_overlaps_second_and_expands_for_ties(monkeypatch):
    monkeypatch.setattr(news, "NEWS_PAGE_SIZE", 2)
    rows = [news_row("a", "2026-03-20"), news_row("b", "2026-03-20"),
            news_row("c", "2026-03-20"), news_row("d", "2026-03-10"), news_row("old", "2026-02-28")]
    params_seen = []
    async def fake(url, params):
        params_seen.append(dict(params))
        found = [r for r in rows if r["date"] < params["enddate"]][:params["count"]]
        return {"appnews": {"newsitems": found}}
    monkeypatch.setattr(http, "get_json", fake)
    out = call(news.get_patch_notes, appid=APPID, start=START, end=END, official_only=True)
    assert out["complete"] and {r["id"] for r in out["notes"]} == {"a", "b", "c", "d"}
    assert any(p["count"] > 2 for p in params_seen)
    assert any(p["enddate"] == rows[0]["date"] + 1 for p in params_seen)


def test_pagination_stalled_and_page_cap_never_claim_complete(monkeypatch):
    async def fake(*args, **kwargs):
        return {"appnews": {"newsitems": [news_row()]}}
    monkeypatch.setattr(http, "get_json", fake)
    out = call(news.get_patch_notes, appid=APPID, start=START, end=END, official_only=True)
    assert out["ok"] and not out["complete"] and out["coverage"]["reason"] == "pagination_stalled"
    monkeypatch.setattr(news, "NEWS_MAX_PAGES", 1)
    out = call(news.get_patch_notes, appid=APPID, start=START, end=END, official_only=True)
    assert not out["complete"] and out["coverage"]["reason"] == "page_limit"


def test_empty_complete_and_failure_partial(monkeypatch):
    calls = []
    async def fake(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise httpx.ConnectError("offline")
        return {"appnews": {"newsitems": [news_row()]}}
    monkeypatch.setattr(http, "get_json", fake)
    out = call(news.get_patch_notes, appid=APPID, start=START, end=END, official_only=True)
    assert not out["complete"] and out["coverage"]["reason"] == "upstream_failure"
    async def empty(*args, **kwargs):
        return {"appnews": {"newsitems": []}}
    monkeypatch.setattr(http, "get_json", empty)
    out = call(news.get_patch_notes, appid=APPID, start=START, end=END, official_only=True)
    assert out["complete"] and out["notes"] == []


def test_snapshot_window_feed_and_new_evidence():
    snap = news_snap([news_row()])
    snap["as_of"] = "2026-04-01"
    http.save_snapshot(APPID, "news_official", snap)
    for _ in range(2):
        research = state()
        research.store.add("other", "x", "", "", "")
        out = call(news.get_patch_notes, research, appid=APPID, start="2026-03-15", end="2026-03-15", official_only=True)
        assert out["from_snapshot"] and out["as_of"] == "2026-04-01"
        assert research.store.get(out["notes"][0]["ref"]).raw_id == "1"
    assert not call(news.get_patch_notes, appid=APPID, start=START, end=END, official_only=False)["ok"]
    assert not call(news.get_patch_notes, appid=APPID, start="2026-02-01", end=END, official_only=True)["ok"]
    snap["coverage"]["complete"] = False
    http.save_snapshot(APPID, "news_official", snap)
    assert not call(news.get_patch_notes, appid=APPID, start=START, end=END, official_only=True)["ok"]


def test_all_feed_snapshot_supports_official_subset():
    http.save_snapshot(APPID, "news_all", news_snap([news_row(), news_row("media", feed="pcgamer")], official=False))
    out = call(news.get_patch_notes, appid=APPID, start=START, end=END, official_only=True)
    assert out["complete"] and len(out["notes"]) == 1 and out["notes"][0]["official"]


@pytest.mark.parametrize("start,end", [("2026-02-30", END), (END, START), ("2026-3-1", END)])
def test_news_invalid_window(start, end):
    assert call(news.get_patch_notes, appid=APPID, start=start, end=end, official_only=True)["error"] == "invalid_arguments"


def test_malformed_live_and_corrupt_snapshot(monkeypatch):
    path = http.SNAPSHOT_DIR / str(APPID) / "news_official.json"
    path.parent.mkdir(parents=True)
    path.write_text("invalid", encoding="utf-8")
    async def malformed(*args, **kwargs):
        return {"appnews": {"newsitems": [{"gid": "bad", "date": "yesterday"}]}}
    monkeypatch.setattr(http, "get_json", malformed)
    assert call(news.get_patch_notes, appid=APPID, start=START, end=END, official_only=True)["error"] == "unavailable"


@pytest.mark.parametrize("tool,args", [
    (news.search_game, {"query": "Baldur's Gate 3", "limit": 5}),
    (news.get_game_info, {"appid": APPID}),
    (news.get_patch_notes, {"appid": APPID, "start": START, "end": END, "official_only": True}),
])
def test_tools_never_write_snapshots(monkeypatch, tool, args):
    def fail(*args, **kwargs):
        raise AssertionError("Tools must not write snapshots")

    async def fake(url, params):
        if url == news.SEARCH_URL:
            return {"items": [{"id": APPID}]}
        if url == news.NEWS_URL:
            return sample("news_1086940")
        return sample("appdetails_1086940")

    monkeypatch.setattr(http, "get_json", fake)
    monkeypatch.setattr(http, "save_snapshot", fail)
    assert call(tool, state(), **args)["ok"]
    assert http.load_snapshot(APPID, "appdetails") is None
