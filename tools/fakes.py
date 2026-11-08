"""Fake data for all 8 tools, so the agents run end to end before the real tools exist.

Turned on by USE_FAKE_TOOLS=1 (off by default). Each fake keeps the real
tool's name, description and parameter schema (it is the real FunctionTool with a different body),
so swapping in the real tools changes nothing on the agent side.

The scenario is fictional: "Ashen Frontier" (appid 2999990), a survival-crafting game whose 1.4
update on 2026-09-09 rewrote the save system (some saves were lost) and added a cash shop. Negative
reviews jump from ~8% to ~41%; Chinese reviewers complain more about the shop and prices, English
reviewers more about saves; 100h+ veterans are the angriest; monthly players fall ~23% in October.
Every result carries "fake": true. Nothing here is real data.
"""

import dataclasses
import json
from datetime import date, timedelta

from agents import FunctionTool

APPID = 2999990
NAME = "Ashen Frontier"
STORE_URL = f"https://store.steampowered.com/app/{APPID}/"
AS_OF = "2026-11-04"

GAMES = [
    {"appid": APPID, "name": NAME, "release_date": "2025-03-18", "price": "$24.99"},
    {"appid": 2999980, "name": "Ashen Frontier: Origins", "release_date": "2022-06-02", "price": "$9.99"},
]

# Weekly review counts (week starting Monday). The 1.4 update lands on 2026-09-09.
WEEKS = [
    ("2026-08-03", 2110, 172), ("2026-08-10", 2380, 205), ("2026-08-17", 1990, 171), ("2026-08-24", 2050, 180),
    ("2026-08-31", 2240, 195), ("2026-09-07", 2950, 2010), ("2026-09-14", 2410, 1630), ("2026-09-21", 1880, 990),
    ("2026-09-28", 1720, 760), ("2026-10-05", 1610, 520), ("2026-10-12", 1540, 410), ("2026-10-19", 1490, 350),
    ("2026-10-26", 1460, 300),
]

# (id, date, language, hours_at_review, voted_up, helpful score, text). The language tags drive
# the fake filtering; the fictional texts are written in English for an all-English codebase.
REVIEWS = [
    ("190000001", "2026-08-14", "english", 64.2, True, 0.81, "Best base-building loop I've played in years. The 1.3 fishing update added a lot."),
    ("190000002", "2026-08-21", "schinese", 37.5, True, 0.74, "The building system is free-form, co-op with friends is a blast, and it is great value."),
    ("190000003", "2026-08-28", "english", 12.0, False, 0.42, "Fun but the late game gets grindy and the frame rate drops in big bases."),
    ("190000004", "2026-09-10", "english", 212.4, False, 0.97, "Lost my 200-hour save after 1.4. The new save system 'migrated' my world into an empty map. No backup, no warning."),
    ("190000005", "2026-09-10", "english", 143.8, False, 0.93, "1.4 wiped two of my three worlds. Support says 'working on it'. Do not update until they fix saves."),
    ("190000006", "2026-09-11", "schinese", 96.1, False, 0.91, "Update 1.4 added a 'Frontier Exchange' where one skin costs 68 yuan, more than the base game. Microtransactions in a paid game are shameless."),
    ("190000007", "2026-09-11", "schinese", 158.0, False, 0.88, "Half my saves are gone, but the shop shipped quickly. Fix saves before monetizing."),
    ("190000008", "2026-09-12", "english", 3.5, False, 0.55, "Bought it on sale, first thing I see is a cash shop button on the main menu. Refunded."),
    ("190000009", "2026-09-13", "tchinese", 120.6, False, 0.79, "After the update my save fails to load, and the official recovery tool cannot save it either."),
    ("190000010", "2026-09-15", "english", 88.0, True, 0.61, "My saves survived. The shop is cosmetic-only and the core game is still great. Review bomb is overblown."),
    ("190000011", "2026-09-18", "english", 175.3, False, 0.86, "The 1.4.1 recovery tool restored my world but lost every chest's contents. 170 hours of loot gone."),
    ("190000012", "2026-09-20", "schinese", 22.4, False, 0.83, "New player here: one look at the shop prices and I stopped wanting to play. The same money buys a complete game."),
    ("190000013", "2026-10-03", "schinese", 64.0, True, 0.58, "Update 1.4.2 cut shop prices by 30%, which shows some good faith; back to a positive review."),
    ("190000014", "2026-10-08", "english", 301.7, False, 0.90, "300 hours in. Saves work again but I don't trust this dev anymore. Shop stays, so the review stays negative."),
    ("190000015", "2026-10-15", "english", 9.8, True, 0.47, "New player here, didn't experience the save issue. Game is fun, ignore the shop."),
    ("190000016", "2026-10-22", "schinese", 140.2, True, 0.52, "Saves work again after the fix, and skipping the shop does not hurt gameplay. Back to the game."),
]

PATCHES = [
    ("5800000001", "2026-08-12", "Update 1.3: Rivers & Rods", True, "Adds fishing, river biomes and 40 new crafting recipes. Performance improvements for large bases."),
    ("5800000002", "2026-09-09", "Update 1.4: New Save System & Frontier Exchange", True,
     "Rewrites the save system for faster loading and cloud sync. Existing worlds are migrated on first launch. "
     "Introduces the Frontier Exchange, an in-game store for cosmetic skins and base decorations."),
    ("5800000003", "2026-09-16", "Hotfix 1.4.1: Save Recovery Tool", True,
     "We're sorry. Some worlds failed to migrate. This hotfix adds a recovery tool that rebuilds worlds from the old "
     "format. Container contents may not be recoverable in all cases."),
    ("5800000004", "2026-10-01", "Update 1.4.2: Exchange price changes", True,
     "Lowers all Frontier Exchange prices by 30% and gives every player who owned the game before 1.4 a free skin pack."),
    ("5800000005", "2026-09-19", "Ashen Frontier's 1.4 update sparks review bomb over lost saves", False,
     "Press coverage: players report lost worlds after the 1.4 save migration; the developer acknowledges the issue."),
]

MONTHS = [  # month, average players, peak players
    ("2026-06", 40100, 61200), ("2026-07", 41800, 63900), ("2026-08", 42300, 66100),
    ("2026-09", 38400, 71500), ("2026-10", 29600, 44800),
]


def _week_end(start: str) -> str:
    return (date.fromisoformat(start) + timedelta(days=6)).isoformat()


def _in(day: str, start: str | None, end: str | None) -> bool:
    return (not start or day >= start) and (not end or day <= end)


def _ok(**fields) -> dict:
    return {"ok": True, "fake": True, "as_of": AS_OF, "from_snapshot": False, **fields}


def _unknown(appid: int, source: str) -> dict:
    return {"ok": False, "fake": True, "error": "no_data", "message": f"The fake data only covers appid {APPID} ({NAME}), not {appid}.", "source": source}


def _review_url(review_id: str) -> str:
    return f"https://steamcommunity.com/profiles/765611980{review_id}/recommended/{APPID}/"


def _language_matches(language: str, wanted: str) -> bool:
    return wanted in ("all", "", None) or language == wanted


# --- Fakes, one per tool. Each takes the run's Research state, then the tool's arguments. ---


def search_game(research, query: str, limit: int = 5) -> dict:
    q = query.lower()
    hits = [g for g in GAMES if q in g["name"].lower() or g["name"].lower() in q]
    return _ok(results=hits[:limit])


def get_review_trend(research, appid: int, language: str = "all") -> dict:
    if appid != APPID:
        return _unknown(appid, "appreviewhistogram")
    series, spikes = [], []
    for i, (start, up, down) in enumerate(WEEKS):
        pct = round(100 * down / (up + down), 1)
        ref = research.store.add(
            "review_trend", f"{APPID}:{start}", STORE_URL, start,
            f"{NAME}, week of {start}: {up} positive, {down} negative reviews ({pct}% negative).",
        )
        series.append({"ref": ref, "period": start, "up": up, "down": down, "pct_negative": pct})
        if i >= 4:
            baseline = round(sum(s["pct_negative"] for s in series[i - 4 : i]) / 4, 1)
            if pct > 2 * baseline:
                spikes.append({"period": start, "pct_negative": pct, "baseline": baseline, "ref": ref})
    # Same chart shape and merge rules as the real tools (tools/steam_reviews.py).
    previous = getattr(research, "chart", None) or {}
    research.chart = {
        "appid": appid, "granularity": "week", "as_of": AS_OF, "from_snapshot": False,
        "series": series,
        "patches": previous.get("patches", []) if previous.get("appid") == appid else [],
    }
    return _ok(appid=appid, granularity="week", language=language, series=series, spikes=spikes, source="appreviewhistogram")


def _matching_reviews(start, end, review_type="all", language="all", min_hours=None, max_hours=None):
    for rid, day, lang, hours, up, score, text in REVIEWS:
        if not _in(day, start, end) or not _language_matches(lang, language):
            continue
        if review_type == "positive" and not up or review_type == "negative" and up:
            continue
        if min_hours is not None and hours < min_hours or max_hours is not None and hours > max_hours:
            continue
        yield rid, day, lang, hours, up, score, text


def sample_reviews(research, appid: int, start: str, end: str, review_type: str = "all", language: str = "all",
                   min_hours: float | None = None, max_hours: float | None = None, limit: int = 40) -> dict:
    if appid != APPID:
        return _unknown(appid, "appreviews")
    matches = sorted(_matching_reviews(start, end, review_type, language, min_hours, max_hours), key=lambda r: -r[5])
    reviews = []
    for rid, day, lang, hours, up, score, text in matches[:limit]:
        ref = research.store.add("review", rid, _review_url(rid), day, text,
                                 {"language": lang, "hours_at_review": hours, "positive": up})
        reviews.append({"ref": ref, "date": day, "language": lang, "positive": up, "hours_at_review": hours, "text": text[:400]})
    return _ok(appid=appid, total_in_range=len(matches), sampled=True, reviews=reviews, source="appreviews")


def review_stats(research, appid: int, start: str, end: str, group_by: str = "language") -> dict:
    if appid != APPID:
        return _unknown(appid, "appreviews")

    def bucket(lang, hours):
        if group_by == "playtime":
            return "<10h" if hours < 10 else "10-100h" if hours <= 100 else ">100h"
        return lang

    groups: dict[str, list[int]] = {}
    for rid, day, lang, hours, up, score, text in _matching_reviews(start, end):
        counts = groups.setdefault(bucket(lang, hours), [0, 0])
        counts[0 if up else 1] += 1
    rows = []
    for name, (up, down) in sorted(groups.items()):
        pct = round(100 * down / (up + down), 1)
        ref = research.store.add(
            "review_stats", f"{APPID}:{start}:{end}:{group_by}:{name}", STORE_URL, end,
            f"{NAME} reviews {start} to {end}, {group_by} = {name}: {up} positive, {down} negative ({pct}% negative, sampled).",
        )
        rows.append({"ref": ref, "group": name, "up": up, "down": down, "pct_negative": pct})
    return _ok(appid=appid, group_by=group_by, groups=rows, sampled=True, source="appreviews")


def get_patch_notes(research, appid: int, start: str, end: str, official_only: bool = True) -> dict:
    if appid != APPID:
        return _unknown(appid, "GetNewsForApp")
    notes = []
    for gid, day, title, official, text in sorted(PATCHES, key=lambda p: p[1]):
        if not _in(day, start, end) or official_only and not official:
            continue
        url = f"https://store.steampowered.com/news/app/{APPID}/view/{gid}"
        ref = research.store.add("news", gid, url, day, f"{title}. {text}")
        notes.append({"ref": ref, "id": gid, "date": day, "title": title, "url": url, "official": official, "summary": text})
    # Same chart merge rules as the real tool (tools/steam_news.py).
    chart = getattr(research, "chart", None)
    if chart is None or chart.get("appid") != appid:
        chart = {"appid": appid, "granularity": None, "as_of": AS_OF, "series": [], "patches": []}
        research.chart = chart
    known = {p["id"] for p in chart["patches"]}
    chart["patches"].extend(
        {"ref": n["ref"], "id": n["id"], "date": n["date"], "title": n["title"], "url": n["url"],
         "official": n["official"], "kind": "patch_notes" if n["official"] else "announcement"}
        for n in notes if n["id"] not in known
    )
    chart["patches"].sort(key=lambda p: (p["date"], p["id"]))
    return _ok(appid=appid, notes=notes, source="GetNewsForApp")


def get_game_info(research, appid: int) -> dict:
    if appid != APPID:
        return _unknown(appid, "appdetails")
    info = {
        "name": NAME, "developer": "Emberline Studio", "publisher": "Emberline Studio", "release_date": "2025-03-18",
        "genres": ["Survival", "Crafting", "Open World"], "price": "$24.99", "discount_pct": 0, "metacritic": 82,
    }
    ref = research.store.add("store", str(APPID), STORE_URL, AS_OF, json.dumps(info))
    return _ok(ref=ref, appid=appid, **info, note="Current price only; no price history.", source="appdetails")


def get_player_stats(research, appid: int) -> dict:
    if appid != APPID:
        return _unknown(appid, "steamspy")
    stats = {
        "owners_estimate": "1,000,000 .. 2,000,000", "average_forever_h": 61.4, "median_forever_h": 23.0,
        "average_2weeks_h": 9.2, "ccu_yesterday": 31200, "current_players": 27850,
        "tags": {"Survival": 2140, "Crafting": 1870, "Open World": 1500, "Base Building": 1320, "Co-op": 1110},
    }
    ref = research.store.add("player_stats", f"steamspy:{APPID}", f"https://steamspy.com/app/{APPID}", AS_OF, json.dumps(stats))
    return _ok(ref=ref, appid=appid, **stats, note="owners_estimate is a SteamSpy estimate, not a sales figure.", source="steamspy + GetNumberOfCurrentPlayers")


def get_player_history(research, appid: int, start: str, end: str) -> dict:
    if appid != APPID:
        return _unknown(appid, "steamcharts")
    months, previous = [], None
    for month, avg, peak in MONTHS:
        gain = round(100 * (avg - previous) / previous, 1) if previous else None
        previous = avg
        if not _in(month, start[:7], end[:7]):
            continue
        ref = research.store.add(
            "player_history", f"{APPID}:{month}", f"https://steamcharts.com/app/{APPID}", month,
            f"{NAME}, {month}: {avg} average players, {peak} peak" + (f", {gain:+}% vs previous month." if gain is not None else "."),
            {"avg": avg, "peak": peak},
        )
        months.append({"ref": ref, "month": month, "avg": avg, "peak": peak, "gain_pct": gain})
    return _ok(appid=appid, months=months, source="steamcharts")


FAKES = {f.__name__: f for f in (search_game, get_review_trend, sample_reviews, review_stats,
                                 get_patch_notes, get_game_info, get_player_stats, get_player_history)}


def fake_tool(real: FunctionTool) -> FunctionTool:
    """The real tool's schema and description, with the fake's body."""
    fake = FAKES[real.name]

    async def invoke(ctx, input_json: str) -> str:
        args = json.loads(input_json or "{}")
        return json.dumps(fake(ctx.context, **args), ensure_ascii=False)

    return dataclasses.replace(real, on_invoke_tool=invoke)
