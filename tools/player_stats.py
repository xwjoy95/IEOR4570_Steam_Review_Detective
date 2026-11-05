"""Player numbers. Owner: C (Player Data Analyst's tools).

Endpoints:
  - steamspy.com/api.php?request=appdetails&appid=<appid>   (third-party estimates, 1 req/s)
      owners (a range string: always say "SteamSpy estimate"), average/median_forever and _2weeks
      (minutes; 0 means "no data", not "0 minutes"), ccu (yesterday's peak), positive/negative,
      price/initialprice/discount, tags {tag: votes}, developer, publisher, genre, languages.
  - api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=<appid>   (now only)
  - steamcharts.com/app/<appid>   no API: parse the monthly table (Month, Avg. Players, Gain,
      % Gain, Peak Players), or try /chart-data.json. May block cloud IPs: rely on snapshots.
  Never SteamDB: it forbids scraping.
"""

from agents import RunContextWrapper, function_tool

from detective.state import Research
from tools.http import not_implemented


@function_tool
async def get_player_stats(ctx: RunContextWrapper[Research], appid: int) -> dict:
    """SteamSpy's owner estimate, playtimes and tags, plus Steam's current player count.

    Args:
        appid: The Steam app id.

    Returns {"ok", "owners_estimate", "average_forever_h", "median_forever_h", "average_2weeks_h",
    "ccu_yesterday", "current_players", "tags", "source", "as_of"}.
    """
    # TODO(C): SteamSpy appdetails + GetNumberOfCurrentPlayers; playtime 0 -> None ("no data").
    return not_implemented("steamspy")


@function_tool
async def get_player_history(ctx: RunContextWrapper[Research], appid: int, start: str, end: str) -> dict:
    """Monthly average and peak concurrent players between start and end, each month stored as evidence.

    Args:
        appid: The Steam app id.
        start: First month, YYYY-MM-DD.
        end: Last month, YYYY-MM-DD.

    Returns {"ok", "months": [{ref, month, avg, peak, gain_pct}], "source", "from_snapshot"}.
    """
    # TODO(C): SteamCharts monthly table; ctx.context.store.add("player_history", month, url, month, row).
    return not_implemented("steamcharts")
