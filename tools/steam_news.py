"""Steam game search, store details, and developer announcements.

Public endpoints require no API key. Tools register evidence in the current run
and read snapshots on failure. scripts/make_snapshot.py writes snapshots through
fetch_game_details and fetch_news_window.
"""

import asyncio
import html
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from html.parser import HTMLParser

import httpx
from agents import RunContextWrapper, function_tool

from detective.state import Research
from tools import http

SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
DETAILS_URL = "https://store.steampowered.com/api/appdetails"
NEWS_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
OFFICIAL_FEED = "steam_community_announcements"
NEWS_PAGE_SIZE = 50
NEWS_MAX_PAGE_SIZE = 200
NEWS_MAX_PAGES = 8
TOOL_BUDGET = 65
_IO_ERRORS = (httpx.HTTPError, asyncio.TimeoutError, ValueError, KeyError, TypeError)


class GameUnavailable(ValueError):
    pass


def _appid(appid: int) -> None:
    if type(appid) is not int or not 0 < appid <= 2**32 - 1:
        raise ValueError("appid must be a positive uint32")


def _window(start: str, end: str) -> tuple[int, int]:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
        raise ValueError("Dates must use YYYY-MM-DD")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError("start must be on or before end")
    return (int(datetime.combine(first, time(), timezone.utc).timestamp()),
            int(datetime.combine(last + timedelta(days=1), time(), timezone.utc).timestamp()))


def _envelope(appid: int, source: str, params: dict, data: dict, **extra) -> dict:
    now = datetime.now(timezone.utc)
    return {"schema_version": 1, "appid": appid, "source": source, "params": params,
            "as_of": now.date().isoformat(), "captured_at": now.isoformat(), "data": data, **extra}


def _valid_snapshot(snap: dict | None, appid: int, source: str) -> bool:
    if not snap or snap.get("appid") != appid or snap.get("source") != source:
        return False
    try:
        date.fromisoformat(snap["as_of"])
        datetime.fromisoformat(snap["captured_at"])
        return isinstance(snap["data"], dict)
    except (KeyError, TypeError, ValueError):
        return False


def _details_data(raw: dict, appid: int) -> dict:
    item = raw[str(appid)]
    if item.get("success") is not True:
        raise GameUnavailable("Steam did not provide this app's details in the US region")
    data = item["data"]
    if not isinstance(data, dict) or not data.get("name") or data.get("steam_appid", appid) != appid:
        raise ValueError("Invalid Steam details response")
    return data


async def fetch_game_details(appid: int) -> dict:
    """Live raw appdetails envelope; usable by the team's snapshot script."""
    _appid(appid)
    params = {"appids": appid, "cc": "US", "l": "english"}
    raw = await http.get_json(DETAILS_URL, params)
    _details_data(raw, appid)
    return _envelope(appid, DETAILS_URL, params, raw)


async def _details(appid: int) -> tuple[dict, bool, list[str]]:
    try:
        snap = await fetch_game_details(appid)
    except GameUnavailable:
        raise
    except _IO_ERRORS:
        snap = http.load_snapshot(appid, "appdetails")
        if not _valid_snapshot(snap, appid, DETAILS_URL) or snap.get("params") != {"appids": appid, "cc": "US", "l": "english"}:
            raise ValueError("Live details unavailable and no matching US/English snapshot exists") from None
        _details_data(snap["data"], appid)
        return snap, True, ["Live details unavailable; using the dated local snapshot."]
    return snap, False, []


def _release(data: dict) -> tuple[str | None, str | None]:
    raw = data.get("release_date", {}).get("date") or None
    if raw:
        for fmt in ("%b %d, %Y", "%d %b, %Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat(), raw
            except ValueError:
                pass
    return raw, raw


def _price(data: dict) -> dict:
    value = data.get("price_overview") or {}
    free = data.get("is_free") is True
    return {"price": "Free" if free else value.get("final_formatted"),
            "price_minor": 0 if free else value.get("final"),
            "initial_price_minor": 0 if free else value.get("initial"),
            "currency": value.get("currency"), "region": "US", "is_free": free,
            "discount_pct": 0 if free else value.get("discount_percent")}


def _candidate(appid: int, snap: dict, from_snapshot: bool) -> dict:
    data = _details_data(snap["data"], appid)
    return {"appid": appid, "name": data["name"], "release_date": _release(data)[0],
            **_price(data), "as_of": snap["as_of"], "from_snapshot": from_snapshot}


def _is_game(data: dict) -> bool:
    return data.get("type") == "game" and not re.search(r"\b(soundtrack|original score)\b", data.get("name", ""), re.I)


def _offline_search(query: str, limit: int) -> list[dict]:
    results = []
    for path in sorted(http.SNAPSHOT_DIR.glob("*/appdetails.json")):
        try:
            appid = int(path.parent.name)
            snap = http.load_snapshot(appid, "appdetails")
            if not _valid_snapshot(snap, appid, DETAILS_URL) or snap.get("params") != {"appids": appid, "cc": "US", "l": "english"}:
                continue
            data = _details_data(snap["data"], appid)
            if _is_game(data) and query.casefold() in data["name"].casefold():
                results.append(_candidate(appid, snap, True))
        except (ValueError, KeyError, TypeError):
            continue
    return results[:limit]


@function_tool
async def search_game(ctx: RunContextWrapper[Research], query: str, limit: int = 5) -> dict:
    """Find base games by name, excluding DLC, soundtracks and tools.

    Args:
        query: The game's name, in any language.
        limit: Maximum matches to return, from 1 to 10.
    """
    query = query.strip()
    if not query or type(limit) is not int or not 1 <= limit <= 10:
        return http.error("invalid_arguments", "Provide a game name and limit from 1 to 10.", "storesearch")
    try:
        raw = await http.get_json(SEARCH_URL, {"term": query, "l": "english", "cc": "US"})
        items = raw["items"]
        if not isinstance(items, list):
            raise ValueError("Invalid search response")
    except _IO_ERRORS:
        results = _offline_search(query, limit)
        if not results:
            return http.error("unavailable", "Live search unavailable; no matching game in local detail snapshots.", "storesearch")
        return {"ok": True, "results": results, "source": "appdetails snapshots",
                "as_of": min(r["as_of"] for r in results), "from_snapshot": True,
                "partial": True, "scope": "Only locally snapshotted US/English games were searched."}

    # storesearch type='app' includes DLC and tools: verify using appdetails.
    ids = list(dict.fromkeys(r["id"] for r in items if isinstance(r, dict) and type(r.get("id")) is int and r["id"] > 0))
    selected = ids[:min(15, limit * 3)]
    async def verify(appid):
        try:
            snap, fallback, _ = await _details(appid)
            data = _details_data(snap["data"], appid)
            return _candidate(appid, snap, fallback) if _is_game(data) else None
        except GameUnavailable:
            return None
        except _IO_ERRORS:
            return "unavailable"
    try:
        checked = await asyncio.wait_for(asyncio.gather(*(verify(i) for i in selected)), TOOL_BUDGET)
    except asyncio.TimeoutError:
        return http.error("unavailable", "Game-type verification exceeded the search budget.", "storesearch")
    results = [r for r in checked if isinstance(r, dict)][:limit]
    failed = "unavailable" in checked
    if failed and not results:
        return http.error("unavailable", "Search candidates could not be verified as base games.", "storesearch")
    return {"ok": True, "results": results, "source": SEARCH_URL,
            "as_of": datetime.now(timezone.utc).date().isoformat() if not results else min(r["as_of"] for r in results),
            "from_snapshot": any(r["from_snapshot"] for r in results),
            "partial": failed or (len(ids) > len(selected) and len(results) < limit)}


@function_tool
async def get_game_info(ctx: RunContextWrapper[Research], appid: int) -> dict:
    """Get US/English store details and cite them. Prices describe collection time only.

    Args:
        appid: The Steam app id.
    """
    try:
        _appid(appid)
    except ValueError as exc:
        return http.error("invalid_arguments", str(exc), "appdetails")
    try:
        snap, fallback, warnings = await _details(appid)
        data = _details_data(snap["data"], appid)
    except GameUnavailable:
        return http.error("not_found", "Steam did not provide this app's details in the US region.", "appdetails")
    except _IO_ERRORS:
        return http.error("unavailable", "Live details unavailable and no valid matching snapshot exists.", "appdetails")
    released, raw_release = _release(data)
    url = f"https://store.steampowered.com/app/{appid}/"
    facts = {"appid": appid, "name": data["name"], "type": data.get("type"),
             "developer": data.get("developers", []), "publisher": data.get("publishers", []),
             "release_date": released, "release_date_raw": raw_release,
             "coming_soon": data.get("release_date", {}).get("coming_soon"),
             "genres": [g["description"] for g in data.get("genres", []) if "description" in g],
             "platforms": data.get("platforms", {}), **_price(data),
             "metacritic": (data.get("metacritic") or {}).get("score"),
             "description": clean_markup(data.get("short_description", "")),
             "price_scope": "Current US store price at collection time; no price history."}
    ref = ctx.context.store.add("store", str(appid), url, snap["as_of"],
                                json.dumps(facts, ensure_ascii=False),
                                {"from_snapshot": fallback, "captured_at": snap["captured_at"]})
    return {"ok": True, **facts, "ref": ref, "url": url, "date": snap["as_of"],
            "source": DETAILS_URL, "as_of": snap["as_of"], "from_snapshot": fallback, "warnings": warnings}


class _PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if self.hidden:
            return
        if tag in {"p", "div", "br", "h1", "h2", "h3", "tr", "li"}:
            self.parts.append("\n- " if tag == "li" else "\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "h1", "h2", "h3", "tr", "li"} and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def clean_markup(text: str) -> str:
    """Keep readable paragraphs, list items and linked words; discard media placeholders."""
    text = re.sub(r"\[(img|previewyoutube|previewimg|video|youtube)[^\]]*\].*?\[/\1\]", "", text, flags=re.I | re.S)
    text = re.sub(r"\{STEAM_CLAN_IMAGE\}[^\s\[]*", "", text)
    text = re.sub(r"\[\*\]", "\n- ", text)
    text = re.sub(r"\[/?(?:p|h[1-6]|list|olist|table|tr|quote)(?:\s[^\]]*)?\]", "\n", text, flags=re.I)
    tags = r"b|i|u|s|strike|url|code|noparse|spoiler|hr|br|td|th|tdborder|thborder|color|size|float|previewyoutube|previewimg|img"
    text = re.sub(rf"\[/?(?:{tags})(?:[=\s][^\]]*)?\]", "", text, flags=re.I)
    parser = _PlainHTML()
    parser.feed(text)
    text = html.unescape("".join(parser.parts)).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


async def fetch_news_window(appid: int, start: str, end: str, official_only: bool = True) -> dict:
    """Live raw news envelope, bounded pagination and explicit window coverage."""
    _appid(appid)
    lower, upper = _window(start, end)
    cursor, count, seen = upper, NEWS_PAGE_SIZE, {}
    complete, reason = False, "page_limit"
    for _ in range(NEWS_MAX_PAGES):
        params = {"appid": appid, "count": count, "maxlength": 0, "enddate": cursor}
        if official_only:
            params["feeds"] = OFFICIAL_FEED
        try:
            raw = await http.get_json(NEWS_URL, params)
            appnews = raw["appnews"]
            rows = appnews["newsitems"]
            if appnews.get("appid", appid) != appid or not isinstance(rows, list):
                raise ValueError("Invalid news response")
            if any(not isinstance(r, dict) or not r.get("gid") or type(r.get("date")) is not int or not isinstance(r.get("contents"), str) for r in rows):
                raise ValueError("Malformed news items")
        except _IO_ERRORS:
            if not seen:
                raise
            reason = "upstream_failure"
            break
        if not rows:
            complete, reason = True, "exhausted"
            break
        # Use an overlapping second; increasing count handles ties larger than one page.
        # Subtracting one here would silently skip same-second announcements.
        new = [r for r in rows if str(r["gid"]) not in seen]
        for row in rows:
            seen[str(row["gid"])] = row
        oldest = min(r["date"] for r in rows)
        if oldest < lower:
            complete, reason = True, "crossed_start"
            break
        next_cursor = oldest + 1
        if next_cursor >= cursor or not new:
            if count < NEWS_MAX_PAGE_SIZE:
                count = min(count * 2, NEWS_MAX_PAGE_SIZE)
                continue
            reason = "pagination_stalled"
            break
        cursor = next_cursor
    rows = sorted((r for r in seen.values() if lower <= r["date"] < upper), key=lambda r: (r["date"], str(r["gid"])))
    return _envelope(appid, NEWS_URL, {"appid": appid, "official_only": official_only},
                     {"appnews": {"appid": appid, "newsitems": rows}},
                     coverage={"start": start, "end": end, "complete": complete, "reason": reason})


def _news_snapshot(appid: int, start: str, end: str, official_only: bool) -> dict | None:
    for name in (["news_official", "news_all"] if official_only else ["news_all"]):
        snap = http.load_snapshot(appid, name)
        if not _valid_snapshot(snap, appid, NEWS_URL):
            continue
        cover, params = snap.get("coverage", {}), snap.get("params", {})
        if params != {"appid": appid, "official_only": name == "news_official"}:
            continue
        try:
            _window(cover["start"], cover["end"])
            if cover.get("complete") is True and cover["start"] <= start and cover["end"] >= end:
                rows = snap["data"]["appnews"]["newsitems"]
                if not isinstance(rows, list) or any(not isinstance(r, dict) or not r.get("gid") or type(r.get("date")) is not int or not isinstance(r.get("contents"), str) for r in rows):
                    continue
                return snap
        except (ValueError, KeyError, TypeError):
            continue
    return None


@function_tool
async def get_patch_notes(ctx: RunContextWrapper[Research], appid: int, start: str, end: str, official_only: bool = True) -> dict:
    """Get dated developer announcements or press coverage; cite original excerpts.

    Args:
        appid: The Steam app id.
        start: First day, YYYY-MM-DD (inclusive, UTC).
        end: Last day, YYYY-MM-DD (inclusive, UTC).
        official_only: Only developer announcements; false also includes press.
    """
    try:
        _appid(appid)
        lower, upper = _window(start, end)
    except (ValueError, OverflowError) as exc:
        return http.error("invalid_arguments", str(exc), "GetNewsForApp")
    fallback, warnings = False, []
    try:
        snap = await asyncio.wait_for(fetch_news_window(appid, start, end, official_only), TOOL_BUDGET)
        if not snap["coverage"]["complete"]:
            saved = _news_snapshot(appid, start, end, official_only)
            if saved:
                snap, fallback = saved, True
    except _IO_ERRORS:
        snap = _news_snapshot(appid, start, end, official_only)
        fallback = True
        if snap is None:
            return http.error("unavailable", "Live news unavailable and no snapshot fully covers this date window and feed.", "GetNewsForApp")
    if fallback:
        warnings.append("Live news unavailable or incomplete; using the dated snapshot covering this window.")
    notes = []
    seen = set()
    for row in snap["data"]["appnews"]["newsitems"]:
        official = row.get("feedname") == OFFICIAL_FEED
        gid = str(row["gid"])
        if gid in seen or not lower <= row["date"] < upper or (official_only and not official):
            continue
        seen.add(gid)
        published = datetime.fromtimestamp(row["date"], timezone.utc).date().isoformat()
        title = clean_markup(row.get("title", ""))
        text = clean_markup(row["contents"])
        url = row.get("url") or f"https://store.steampowered.com/news/app/{appid}/view/{gid}"
        kind = "patch_notes" if "patchnotes" in (row.get("tags") or []) or re.search(r"\b(patch|hotfix)\b|\u8865\u4e01|\u4fee\u590d", title, re.I) else "announcement"
        ref = ctx.context.store.add("news", gid, url, published, title + "\n\n" + text,
                                    {"official": official, "kind": kind, "feed": row.get("feedname"),
                                     "as_of": snap["as_of"], "from_snapshot": fallback})
        notes.append({"ref": ref, "id": gid, "date": published, "title": title, "url": url,
                      "official": official, "kind": kind, "feed": row.get("feedname"),
                      "summary": text[:1200], "summary_is_excerpt": True, "truncated": len(text) > 1200})
    notes.sort(key=lambda n: (n["date"], n["id"]))
    complete = snap["coverage"]["complete"]
    if not complete:
        warnings.append("Partial coverage: missing announcements cannot be ruled out.")
    return {"ok": True, "appid": appid, "notes": notes, "source": NEWS_URL,
            "as_of": snap["as_of"], "from_snapshot": fallback, "complete": complete,
            "coverage": snap["coverage"], "requested_window": {"start": start, "end": end}, "warnings": warnings}
