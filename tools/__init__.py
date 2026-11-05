"""Data tools. Each teammate owns one module; agents import the tools from here.

Every tool returns {"ok": True, ...} or {"ok": False, "error", "message", "source"}.
Every citable item carries "ref" (an evidence id from the run's EvidenceStore), plus url and date.
Dates are YYYY-MM-DD.

USE_FAKE_TOOLS=1 (the default for now) swaps every tool's body for tools/fakes.py, keeping its name
and schema. Set USE_FAKE_TOOLS=0 once the real tools work.
"""

import os

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

USE_FAKE_TOOLS = os.environ.get("USE_FAKE_TOOLS", "1") == "1"

if USE_FAKE_TOOLS:
    from tools.fakes import fake_tool

    _TOOLS = {tool.name: fake_tool(tool) for tool in _REAL}
else:
    _TOOLS = {tool.name: tool for tool in _REAL}

search_game = _TOOLS["search_game"]
get_game_info = _TOOLS["get_game_info"]
get_patch_notes = _TOOLS["get_patch_notes"]
get_review_trend = _TOOLS["get_review_trend"]
sample_reviews = _TOOLS["sample_reviews"]
review_stats = _TOOLS["review_stats"]
get_player_stats = _TOOLS["get_player_stats"]
get_player_history = _TOOLS["get_player_history"]

__all__ = list(_TOOLS)
