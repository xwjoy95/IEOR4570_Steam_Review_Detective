"""Data tools. Each teammate owns one module; agents import the tools from here.

Every tool returns {"ok": True, ...} or {"ok": False, "error", "message", "source"}.
Every citable item carries "ref" (an evidence id from the run's EvidenceStore), plus url and date.
Dates are YYYY-MM-DD.

The real tools are the default. USE_FAKE_TOOLS=1 swaps every tool's body for tools/fakes.py (a
fictional game, no network), keeping its name and schema: handy for working on the agents offline.
"""

import dataclasses
import json
import os
import re

from agents import FunctionTool

from tools import player_stats, steam_news, steam_reviews

_REAL = [
    steam_news.search_game,
    steam_news.get_game_info,
    steam_news.get_patch_notes,
    steam_reviews.get_review_trend,
    steam_reviews.sample_reviews,
    steam_reviews.review_stats,
    player_stats.get_player_stats,
    player_stats.get_player_history,
]

USE_FAKE_TOOLS = os.environ.get("USE_FAKE_TOOLS", "0") == "1"

_DATE = re.compile(r"\s*(\d{4}-\d{2}-\d{2})\S*.*", re.S)


def clean_dates(tool: FunctionTool) -> FunctionTool:
    """Repair date arguments before the tool sees them.

    Gemini flash-lite sometimes glues the next parameter onto a date, e.g. end="2026-11-06,language:",
    and then retries the same broken call. Keep the leading YYYY-MM-DD of start/end; leave everything
    else to the tool's own validation.
    """
    invoke = tool.on_invoke_tool

    async def on_invoke_tool(ctx, input_json: str):
        try:
            args = json.loads(input_json or "{}")
        except ValueError:
            return await invoke(ctx, input_json)
        for key in ("start", "end"):
            if isinstance(args.get(key), str) and (m := _DATE.fullmatch(args[key])):
                args[key] = m.group(1)
        return await invoke(ctx, json.dumps(args, ensure_ascii=False))

    return dataclasses.replace(tool, on_invoke_tool=on_invoke_tool)


if USE_FAKE_TOOLS:
    from tools.fakes import fake_tool

    _TOOLS = {tool.name: clean_dates(fake_tool(tool)) for tool in _REAL}
else:
    _TOOLS = {tool.name: clean_dates(tool) for tool in _REAL}

search_game = _TOOLS["search_game"]
get_game_info = _TOOLS["get_game_info"]
get_patch_notes = _TOOLS["get_patch_notes"]
get_review_trend = _TOOLS["get_review_trend"]
sample_reviews = _TOOLS["sample_reviews"]
review_stats = _TOOLS["review_stats"]
get_player_stats = _TOOLS["get_player_stats"]
get_player_history = _TOOLS["get_player_history"]

__all__ = list(_TOOLS)
