"""Player numbers. Owner: C (Player Data Analyst's tools).

Endpoints (no key):
  - steamspy.com/api.php?request=appdetails&appid=<appid>   (third-party estimates, 1 req/s)
      owners (a range string: always say "SteamSpy estimate"), average/median_forever and _2weeks
      (minutes; 0 means "no data", not "0 minutes"), ccu (yesterday's peak), positive/negative,
      price/initialprice/discount, tags {tag: votes}, developer, publisher, genre, languages.
      An unknown appid still returns 200, with name null.
  - api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=<appid>   (now only)
  - steamcharts.com/app/<appid>   no API: we parse the monthly table (Month, Avg. Players, Gain,
      % Gain, Peak Players). Returns 404/500 for unknown apps, DLC and tools. May block cloud IPs,
      so get_player_history falls back to data/snapshots/<appid>/steamcharts.json.
  Never SteamDB: it forbids scraping.

Tools never write snapshots; scripts/make_snapshot.py does, through the fetch_* functions below.
"""

import html
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser

import httpx
from agents import RunContextWrapper, function_tool

from detective.state import Research
from tools import http

STEAMSPY_URL = "https://steamspy.com/api.php"
CURRENT_PLAYERS_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
STEAMCHARTS_URL = "https://steamcharts.com/app/{appid}"
MAX_TAGS = 10
_IO_ERRORS = (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError)


class NoData(Exception):
    """The source answered, but has nothing for this appid (SteamSpy returns 200 with name null)."""


# --- Validation and snapshot envelopes (same shape as B's) ---


def _appid(appid: int) -> None:
    if type(appid) is not int or not 0 < appid <= 2**32 - 1:
        raise ValueError("appid must be a positive integer")


def _month_window(start: str, end: str) -> tuple[str, str]:
    """YYYY-MM-DD dates -> inclusive "YYYY-MM" bounds."""
    for value in (start, end):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
            raise ValueError("Dates must use YYYY-MM-DD")
        date.fromisoformat(value)
    if start > end:
        raise ValueError("start must be on or before end")
    return start[:7], end[:7]


def _envelope(appid: int, source: str, params: dict, data: dict) -> dict:
    now = datetime.now(timezone.utc)
    return {"schema_version": 1, "appid": appid, "source": source, "params": params,
            "as_of": now.date().isoformat(), "captured_at": now.isoformat(), "data": data}


def _valid_snapshot(snap: dict | None, appid: int, source: str) -> bool:
    return bool(snap) and snap.get("appid") == appid and snap.get("source") == source \
        and isinstance(snap.get("data"), dict) and isinstance(snap.get("as_of"), str)


# --- SteamSpy + current players ---


def _hours(minutes) -> float | None:
    """SteamSpy minutes -> hours. 0 or missing means SteamSpy has no data, not zero playtime."""
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        return None
    return round(minutes / 60, 1) if minutes > 0 else None


def parse_steamspy(raw: dict, appid: int) -> dict:
    """SteamSpy appdetails JSON -> the fields the agents use. Raises NoData for unknown apps."""
    if not isinstance(raw, dict) or not raw.get("name") or raw.get("appid") != appid:
        raise NoData("SteamSpy has no data for this appid")
    tags = raw.get("tags") if isinstance(raw.get("tags"), dict) else {}
    top_tags = dict(sorted(tags.items(), key=lambda kv: -kv[1])[:MAX_TAGS])
    return {
        "name": raw["name"],
        "owners_estimate": raw.get("owners") or None,
        "average_forever_h": _hours(raw.get("average_forever")),
        "median_forever_h": _hours(raw.get("median_forever")),
        "average_2weeks_h": _hours(raw.get("average_2weeks")),
        "median_2weeks_h": _hours(raw.get("median_2weeks")),
        "ccu_yesterday": raw.get("ccu") if isinstance(raw.get("ccu"), int) and raw["ccu"] > 0 else None,
        "steamspy_positive": raw.get("positive"),
        "steamspy_negative": raw.get("negative"),
        "tags": top_tags,
    }


def owners_upper_bound(owners: str | None) -> int | None:
    """'20,000,000 .. 50,000,000' -> 50000000; None if it can't be read."""
    try:
        return int(str(owners).split("..")[-1].replace(",", "").strip())
    except ValueError:
        return None


def parse_current_players(raw: dict) -> int:
    response = raw["response"]
    if response.get("result") != 1 or type(response.get("player_count")) is not int:
        raise NoData("Steam has no current player count for this appid")
    return response["player_count"]


async def fetch_steamspy(appid: int) -> dict:
    """Live raw SteamSpy appdetails envelope; usable by scripts/make_snapshot.py."""
    _appid(appid)
    params = {"request": "appdetails", "appid": appid}
    raw = await http.get_json(STEAMSPY_URL, params, host_limit="steamspy")
    parse_steamspy(raw, appid)  # refuse to snapshot an empty answer
    return _envelope(appid, STEAMSPY_URL, params, raw)


async def fetch_current_players(appid: int) -> int:
    _appid(appid)
    raw = await http.get_json(CURRENT_PLAYERS_URL, {"appid": appid}, host_limit="steam_api")
    return parse_current_players(raw)


# --- SteamCharts ---


class _MonthTable(HTMLParser):
    """Collects the text of each <td> in SteamCharts' first table.common-table."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._in_table = self._done = False
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        if tag == "table" and "common-table" in (dict(attrs).get("class") or ""):
            self._in_table = True
        elif self._in_table and tag == "tr":
            self.rows.append([])
        elif self._in_table and tag == "td":
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None and self.rows:
            self.rows[-1].append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "table" and self._in_table:
            self._in_table, self._done = False, True

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _number(text: str) -> float | None:
    text = html.unescape(text).replace(",", "").replace("%", "").replace("+", "").strip()
    try:
        return float(text)
    except ValueError:
        return None  # "-" on the first month


def parse_steamcharts(page: str) -> list[dict]:
    """SteamCharts app page HTML -> [{month: "YYYY-MM", avg, peak, gain, gain_pct}], oldest first.
    Skips the rolling "Last 30 Days" row. Raises ValueError if the table isn't there."""
    parser = _MonthTable()
    parser.feed(page)
    months = []
    for cells in parser.rows:
        if len(cells) != 5:
            continue  # the header row has <th>, not <td>
        try:
            month = datetime.strptime(cells[0], "%B %Y").strftime("%Y-%m")
        except ValueError:
            continue  # "Last 30 Days"
        avg, gain, gain_pct, peak = (_number(c) for c in cells[1:])
        if avg is None or peak is None:
            continue
        months.append({"month": month, "avg": round(avg), "peak": int(peak),
                       "gain": None if gain is None else round(gain), "gain_pct": gain_pct})
    if not months:
        raise ValueError("No monthly player table on the SteamCharts page")
    return sorted(months, key=lambda m: m["month"])


async def fetch_steamcharts(appid: int) -> dict:
    """Live SteamCharts monthly table as an envelope ({"data": {"months": [...]}}); for make_snapshot.py."""
    _appid(appid)
    url = STEAMCHARTS_URL.format(appid=appid)
    page = await http.get_text(url, host_limit="steamcharts")
    return _envelope(appid, url, {"appid": appid}, {"months": parse_steamcharts(page)})


# --- Tools ---


@function_tool
async def get_player_stats(ctx: RunContextWrapper[Research], appid: int) -> dict:
    """SteamSpy's owner estimate, playtimes and tags, plus Steam's current player count.

    Args:
        appid: The Steam app id.

    Returns {"ok", "ref", "owners_estimate", "average_forever_h", "median_forever_h", "average_2weeks_h",
    "median_2weeks_h", "ccu_yesterday", "current_players", "tags", "source", "as_of", "from_snapshot"}.
    Playtimes and counts are null when SteamSpy has no data.
    """
    source = "steamspy + GetNumberOfCurrentPlayers"
    try:
        _appid(appid)
    except ValueError as exc:
        return http.error("invalid_argument", str(exc), source)

    warnings, from_snapshot = [], False
    try:
        snap = await fetch_steamspy(appid)
    except NoData as exc:
        return http.error("no_data", str(exc), STEAMSPY_URL)
    except _IO_ERRORS:
        snap = http.load_snapshot(appid, "steamspy")
        if not _valid_snapshot(snap, appid, STEAMSPY_URL):
            return http.error("unavailable", "SteamSpy is unreachable and there is no snapshot for this appid.", STEAMSPY_URL)
        from_snapshot = True
        warnings.append(f"SteamSpy unreachable; using the snapshot from {snap['as_of']}.")
    try:
        stats = parse_steamspy(snap["data"], appid)
    except NoData as exc:
        return http.error("no_data", str(exc), STEAMSPY_URL)

    try:
        current = await fetch_current_players(appid)
    except (*_IO_ERRORS, NoData):
        current = None
        warnings.append("Steam's current player count is unavailable.")

    # SteamSpy lags for new releases: an owner range below the players online right now can't be right.
    online = max(v for v in (current, stats["ccu_yesterday"], 0) if v is not None)
    upper = owners_upper_bound(stats["owners_estimate"])
    owners_unreliable = upper is not None and online > upper
    if owners_unreliable:
        warnings.append(f"SteamSpy's owner estimate ({stats['owners_estimate']}) is below the {online} players online, "
                        "so it is out of date (common for new releases). Don't report it as a figure.")

    as_of = snap["as_of"]
    text = (f"{stats['name']} (SteamSpy, {as_of}): owners estimate {stats['owners_estimate']}; "
            f"yesterday's peak concurrent players {stats['ccu_yesterday']}; "
            f"average playtime {stats['average_forever_h']} h, median {stats['median_forever_h']} h "
            f"(None = no data); current players on Steam {current}; top tags {', '.join(stats['tags'])}."
            + (" The owners estimate is below the players online, so SteamSpy is out of date for this game." if owners_unreliable else ""))
    ref = ctx.context.store.add("player_stats", f"steamspy:{appid}:{as_of}", f"https://steamspy.com/app/{appid}", as_of, text,
                                meta={"owners_estimate": stats["owners_estimate"], "current_players": current})
    return {"ok": True, "ref": ref, "appid": appid, **stats, "current_players": current,
            "owners_unreliable": owners_unreliable,
            "note": "owners_estimate is a SteamSpy estimate range, not a sales figure. Null playtime means no data.",
            "warnings": warnings, "source": source, "as_of": as_of, "from_snapshot": from_snapshot}


@function_tool
async def get_player_history(ctx: RunContextWrapper[Research], appid: int, start: str, end: str) -> dict:
    """Monthly average and peak concurrent players between start and end (SteamCharts), each month
    stored as evidence.

    Args:
        appid: The Steam app id.
        start: First month, YYYY-MM-DD (the day is ignored).
        end: Last month, YYYY-MM-DD (the day is ignored).

    Returns {"ok", "months": [{ref, month, avg, peak, gain_pct}], "coverage": [first, last],
    "source", "as_of", "from_snapshot"}.
    """
    url = STEAMCHARTS_URL.format(appid=appid)
    try:
        _appid(appid)
        first, last = _month_window(start, end)
    except ValueError as exc:
        return http.error("invalid_argument", str(exc), "steamcharts")

    from_snapshot = False
    try:
        snap = await fetch_steamcharts(appid)
    except _IO_ERRORS:
        snap = http.load_snapshot(appid, "steamcharts")
        if not _valid_snapshot(snap, appid, url):
            return http.error("unavailable", "SteamCharts is unreachable or has no page for this appid, and there is "
                              "no snapshot. Historical player counts are not available.", "steamcharts")
        from_snapshot = True

    rows = snap["data"].get("months") or []
    coverage = [rows[0]["month"], rows[-1]["month"]] if rows else []
    picked = [r for r in rows if first <= r["month"] <= last]
    if not picked:
        return http.error("no_data", f"No SteamCharts months between {first} and {last}; it covers "
                          f"{' to '.join(coverage) or 'nothing'}.", "steamcharts")

    months = []
    for r in picked:
        change = f", {r['gain_pct']:+}% vs previous month" if r["gain_pct"] is not None else ""
        ref = ctx.context.store.add("player_history", f"{appid}:{r['month']}", url, r["month"],
                                    f"{r['month']}: {r['avg']} average concurrent players, {r['peak']} peak{change} (SteamCharts).",
                                    meta={"avg": r["avg"], "peak": r["peak"]})
        months.append({"ref": ref, "month": r["month"], "avg": r["avg"], "peak": r["peak"], "gain_pct": r["gain_pct"]})
    return {"ok": True, "appid": appid, "months": months, "coverage": coverage, "source": "steamcharts",
            "as_of": snap["as_of"], "from_snapshot": from_snapshot}
