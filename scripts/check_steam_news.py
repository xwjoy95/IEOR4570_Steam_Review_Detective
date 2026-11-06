"""Validate B's public tools live, then replay their snapshots with HTTP disabled.

Usage (no Steam or model key):
  uv run scripts/check_steam_news.py --start 2026-03-01 --end 2026-03-31
Writes public validation metadata under docs/, not credentials or model traces.
"""
import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import patch

# Support execution by file path, as used by the other project scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from agents.tool_context import ToolContext

from evidence import EvidenceStore
from tools import http, steam_news


class PagePosts(HTMLParser):
    """Steam embeds the original announcement in this public HTML attribute."""
    def __init__(self):
        super().__init__()
        self.posts = []

    def handle_starttag(self, tag, attrs):
        value = dict(attrs).get("data-partnereventstore")
        if value:
            self.posts.extend(json.loads(value))


def normalize(text):
    return re.sub(r"\s+", " ", steam_news.clean_markup(text)).strip()


async def invoke(tool, state, **args):
    ctx = ToolContext(context=state, tool_name=tool.name, tool_call_id="validation", tool_arguments=json.dumps(args))
    out = await tool.on_invoke_tool(ctx, json.dumps(args))
    return json.loads(out) if isinstance(out, str) else out


async def main(args):
    state = SimpleNamespace(store=EvidenceStore())
    try:
        games = await invoke(steam_news.search_game, state, query=args.query, limit=5)
        if not games.get("ok") or not games["results"]:
            raise RuntimeError("Live search did not return a game")
        game = games["results"][0]
        appid = game["appid"]
        info = await invoke(steam_news.get_game_info, state, appid=appid)
        notes = await invoke(steam_news.get_patch_notes, state, appid=appid, start=args.start, end=args.end, official_only=True)
        if not info.get("ok") or not notes.get("ok") or not notes["complete"]:
            raise RuntimeError("Live details/news did not complete")
        if games["from_snapshot"] or info["from_snapshot"] or notes["from_snapshot"]:
            raise RuntimeError("Live validation unexpectedly used a snapshot")
        refs_valid = all(state.store.get(n["ref"]) for n in notes["notes"])
        page_check = None
        if notes["notes"]:
            note = notes["notes"][0]
            text = await http.get_text(note["url"])
            parser = PagePosts()
            parser.feed(text)
            original = next(p["announcement_body"] for p in parser.posts
                            if p.get("announcement_body", {}).get("headline") == note["title"])
            evidence = state.store.get(note["ref"])
            page_check = {"url": note["url"], "date": note["date"], "title": note["title"],
                          "title_found": note["title"] in text,
                          "excerpt_found": normalize(note["summary"]) in normalize(original["body"]),
                          "date_matches": datetime.fromtimestamp(original["posttime"], timezone.utc).date().isoformat() == note["date"],
                          "evidence_matches": evidence.date == note["date"] and note["summary"] in evidence.text}
            if not all(page_check[k] for k in ["title_found", "excerpt_found", "date_matches", "evidence_matches"]):
                raise RuntimeError("Original announcement did not match the tool excerpt/date")
        async def offline(*a, **kw):
            raise httpx.ConnectError("Simulated offline validation")
        replay = SimpleNamespace(store=EvidenceStore())
        replay.store.add("validation", "seed", "", "", "Ensure refs are registered anew")
        with patch.object(http, "get_json", offline):
            old_games = await invoke(steam_news.search_game, replay, query=args.query, limit=5)
            old_info = await invoke(steam_news.get_game_info, replay, appid=appid)
            old_notes = await invoke(steam_news.get_patch_notes, replay, appid=appid, start=args.start, end=args.end, official_only=True)
        for result in [old_games, old_info, old_notes]:
            if not result.get("ok") or not result.get("from_snapshot"):
                raise RuntimeError("Offline snapshot replay failed")
        assert all(replay.store.get(n["ref"]) for n in old_notes["notes"])
        assert replay.store.get(old_info["ref"])
        assert [n["id"] for n in old_notes["notes"]] == [n["id"] for n in notes["notes"]]
        assert notes["as_of"] == old_notes["as_of"]
        report = {"checked_at": datetime.now(timezone.utc).isoformat(), "appid": appid, "name": game["name"],
                  "window": {"start": args.start, "end": args.end}, "live": True,
                  "notes_count": len(notes["notes"]), "complete": notes["complete"],
                  "refs_valid": refs_valid, "page_check": page_check,
                  "offline_replay": True, "replay_as_of": old_notes["as_of"],
                  "model_calls": 0, "steam_key_used": False}
        output = Path(__file__).resolve().parents[1] / "docs" / "b_validation.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        await http.close_client()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="Baldur's Gate 3")
    parser.add_argument("--start", default="2026-03-01")
    parser.add_argument("--end", default="2026-03-31")
    asyncio.run(main(parser.parse_args()))
