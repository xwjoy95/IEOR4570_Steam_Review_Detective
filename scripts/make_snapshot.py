"""Save every data source for the demo games to data/snapshots/<appid>/, so the demo and the README's
sample questions still work when Steam rate-limits us, SteamCharts blocks Cloud Run, or an unofficial
endpoint breaks. The tools only read these files (fallback); this script is the only writer.

Owner: C.

Usage:
  uv run scripts/make_snapshot.py 1086940 --start 2026-08-01 --end 2026-10-31
  uv run scripts/make_snapshot.py 1086940 --only steamspy steamcharts

Sources (names match what each tool loads):
  B  appdetails         tools.steam_news.fetch_game_details
  B  news_official      tools.steam_news.fetch_news_window (saved only if the window is complete)
  A  review_histogram_all   appreviewhistogram, as get_review_trend loads it
  A  review_stats_<lang>_<YYYY_MM>   tools.steam_reviews.fetch_review_pages, one per month and language
  A  reviews_<type>_<lang>_<start>_<end>   review samples for sample_reviews: negative and positive,
                        languages all / english / schinese, over the whole --start..--end window
  C  steamspy           tools.player_stats.fetch_steamspy
  C  steamcharts        tools.player_stats.fetch_steamcharts
sample_reviews also falls back to any saved sample whose window overlaps the request, narrowed to it.
"""

import argparse
import asyncio
import sys
from calendar import monthrange
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import http  # noqa: E402
from tools.player_stats import fetch_steamcharts, fetch_steamspy  # noqa: E402
from tools.steam_news import fetch_game_details, fetch_news_window  # noqa: E402
from tools.steam_reviews import fetch_review_pages, review_snapshot_name  # noqa: E402

SOURCES = ["appdetails", "news_official", "review_histogram_all", "review_samples", "review_stats", "steamspy", "steamcharts"]
SAMPLE_TYPES = ("negative", "positive")
SAMPLE_PAGES = 3  # up to 300 reviews per type and language, the same depth sample_reviews fetches live
REVIEW_LANGUAGES = ("english", "schinese", "all")
HISTOGRAM_URL = "https://store.steampowered.com/appreviewhistogram/{appid}"


def months(start: str, end: str):
    """Yield (first_day, last_day) for every calendar month between start and end."""
    y, m = int(start[:4]), int(start[5:7])
    while f"{y:04d}-{m:02d}" <= end[:7]:
        yield f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{monthrange(y, m)[1]:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


async def snapshot_game(appid: int, start: str, end: str, only: list[str]) -> list[str]:
    """Fetch and save each requested source. Returns one status line per snapshot."""
    lines = []

    async def save(name, make):
        try:
            value = await make()
            if value is None:
                return
            path = http.save_snapshot(appid, name, value)
            lines.append(f"  saved  {name:<32} {path.relative_to(http.SNAPSHOT_DIR.parent.parent)}")
        except Exception as exc:  # one source failing must not stop the others
            lines.append(f"  FAILED {name:<32} {type(exc).__name__}: {str(exc)[:160]}")

    if "appdetails" in only:
        await save("appdetails", lambda: fetch_game_details(appid))

    if "news_official" in only:
        async def news():
            snap = await fetch_news_window(appid, start, end, official_only=True)
            if not snap["coverage"]["complete"]:
                raise RuntimeError(f"news window incomplete ({snap['coverage'].get('reason')}); not saved")
            return snap
        await save("news_official", news)

    if "review_histogram_all" in only:
        async def histogram():
            raw = await http.get_json(HISTOGRAM_URL.format(appid=appid), {"l": "english", "review_score_preference": 0},
                                      host_limit="steam_store")
            if raw.get("success") != 1:
                raise RuntimeError("Steam returned an unsuccessful histogram")
            return {"params": {"appid": appid, "language": "all"}, "data": raw}
        await save("review_histogram_all", histogram)

    if "review_samples" in only:
        for review_type in SAMPLE_TYPES:
            for language in REVIEW_LANGUAGES:
                query = {"appid": appid, "start": start, "end": end, "review_type": review_type, "language": language}

                async def sample(query=query):
                    return {"params": query, "data": await fetch_review_pages(**query, max_pages=SAMPLE_PAGES)}
                await save(review_snapshot_name(review_type, language, start, end), sample)

    if "review_stats" in only:
        for first, last in months(start, end):
            for language in REVIEW_LANGUAGES:
                query = {"appid": appid, "start": first, "end": last, "review_type": "all", "language": language}

                async def stats(query=query):
                    return {"params": query, "data": await fetch_review_pages(**query, max_pages=3)}
                await save(f"review_stats_{language}_{first[:7].replace('-', '_')}", stats)

    if "steamspy" in only:
        await save("steamspy", lambda: fetch_steamspy(appid))
    if "steamcharts" in only:
        await save("steamcharts", lambda: fetch_steamcharts(appid))
    return lines


async def main(appids: list[int], start: str, end: str, only: list[str]) -> None:
    try:
        for appid in appids:
            print(f"{appid} ({start} to {end})")
            for line in await snapshot_game(appid, start, end, only):
                print(line)
    finally:
        await http.close_client()


if __name__ == "__main__":
    today = date.today().isoformat()
    parser = argparse.ArgumentParser(description="Save demo games' data to data/snapshots/<appid>/.")
    parser.add_argument("appids", nargs="+", type=int)
    parser.add_argument("--start", default=f"{int(today[:4]) - (1 if int(today[5:7]) <= 3 else 0)}-"
                        f"{(int(today[5:7]) - 4) % 12 + 1:02d}-01", help="YYYY-MM-DD (default: 3 months ago)")
    parser.add_argument("--end", default=today, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--only", nargs="+", choices=SOURCES, default=SOURCES, help="save only these sources")
    args = parser.parse_args()
    date.fromisoformat(args.start), date.fromisoformat(args.end)
    asyncio.run(main(args.appids, args.start, args.end, args.only))
