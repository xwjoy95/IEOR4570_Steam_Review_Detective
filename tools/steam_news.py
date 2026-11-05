"""Patch notes, game info, and name -> appid. Owner: B (Patch Historian's tools + search_game).

Endpoints (no key):
  - api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid=<appid>
      count, maxlength (0 = full text), enddate (page backwards), feeds=steam_community_announcements
      for official posts only. Fields: gid, title, url, date, contents (BBCode), feedlabel, tags
      (patch notes usually tagged "patchnotes").
  - store.steampowered.com/api/appdetails?appids=<appid>&cc=us
      name, developers, publishers, release_date, genres, platforms, price_overview, metacritic.
      Current price only, no history.
  - store.steampowered.com/api/storesearch/?term=<name>&l=english&cc=US
"""

from agents import RunContextWrapper, function_tool

from detective.state import Research
from tools.http import not_implemented


@function_tool
async def search_game(ctx: RunContextWrapper[Research], query: str, limit: int = 5) -> dict:
    """Find Steam games by name. Use it to turn the game the user named into an appid.

    Args:
        query: The game's name, in any language.
        limit: How many matches to return, at most.

    Returns {"ok", "results": [{appid, name, release_date, price}]}.
    """
    # TODO(B): storesearch; drop DLC and soundtracks.
    return not_implemented("storesearch")


@function_tool
async def get_patch_notes(ctx: RunContextWrapper[Research], appid: int, start: str, end: str, official_only: bool = True) -> dict:
    """Announcements and patch notes published between start and end, each stored as evidence.

    Args:
        appid: The Steam app id.
        start: First day, YYYY-MM-DD.
        end: Last day, YYYY-MM-DD.
        official_only: Only the developer's own announcements (no press coverage).

    Returns {"ok", "notes": [{ref, date, title, url, summary}]}.
    """
    # TODO(B): page back with enddate; strip BBCode; ctx.context.store.add("news", gid, url, date, text).
    return not_implemented("GetNewsForApp")


@function_tool
async def get_game_info(ctx: RunContextWrapper[Research], appid: int) -> dict:
    """A game's store details: name, developer, publisher, release date, genres, current price and
    discount, Metacritic score.

    Args:
        appid: The Steam app id.

    Returns {"ok", "name", "developer", "publisher", "release_date", "genres", "price", "discount_pct", "metacritic"}.
    """
    # TODO(B): appdetails, flatten the fields above.
    return not_implemented("appdetails")
