"""Player reviews. Owner: A (Review Analyst's tools).

Endpoints (no key):
  - store.steampowered.com/appreviews/<appid>?json=1
      filter=recent|all, review_type=all|positive|negative, language=all|english|schinese|...,
      num_per_page<=100, cursor (first page "*", then the returned cursor, URL-encoded),
      day_range (with filter=all, <=365), filter_offtopic_activity=0 to include review bombs.
      Useful fields: recommendationid, review, voted_up, timestamp_created, language,
      author.playtime_at_review / playtime_forever (minutes), votes_up, weighted_vote_score,
      written_during_early_access, primarily_steam_deck, received_for_free; query_summary on page 1.
  - store.steampowered.com/appreviewhistogram/<appid>?l=english&review_score_preference=0
      Unofficial. rollups (weekly/monthly up/down) + recent (daily, last 30 days).
      Fallback if it breaks: count fetched reviews per day yourself.

Review link for evidence: steamcommunity.com/profiles/<steamid>/recommended/<appid>/
"""

from agents import RunContextWrapper, function_tool

from detective.state import Research
from tools.http import not_implemented


@function_tool
async def get_review_trend(ctx: RunContextWrapper[Research], appid: int, language: str = "all") -> dict:
    """Weekly or monthly positive/negative review counts for a game, and the periods where the
    negative share jumped.

    Args:
        appid: The Steam app id.
        language: "all", "english", "schinese", "tchinese", ...

    Returns {"ok", "appid", "granularity", "series": [{period, up, down, pct_negative}],
    "spikes": [{period, pct_negative, baseline}], "source", "as_of", "from_snapshot"}.
    """
    # TODO(A): call appreviewhistogram; compute pct_negative and spikes (e.g. > 2x the previous
    # 4 periods' average) in code, so the model doesn't do arithmetic. Set ctx.context.chart.
    return not_implemented("appreviewhistogram")


@function_tool
async def sample_reviews(
    ctx: RunContextWrapper[Research],
    appid: int,
    start: str,
    end: str,
    review_type: str = "negative",
    language: str = "all",
    min_hours: float | None = None,
    max_hours: float | None = None,
    limit: int = 40,
) -> dict:
    """Representative reviews written between start and end, each stored as evidence.

    Args:
        appid: The Steam app id.
        start: First day, YYYY-MM-DD.
        end: Last day, YYYY-MM-DD.
        review_type: "all", "positive" or "negative".
        language: "all", "english", "schinese", ...
        min_hours: Only reviewers with at least this many hours at review time.
        max_hours: Only reviewers with at most this many hours at review time.
        limit: How many reviews to return, at most.

    Returns {"ok", "total_in_range", "reviews": [{ref, date, positive, hours_at_review, text(<=400 chars)}]}.
    """
    # TODO(A): page with filter=recent until before start (sample, don't fetch everything), filter by
    # hours, pick the most helpful (weighted_vote_score), ctx.context.store.add(..., meta={language, hours_at_review, positive}) each one
    # (the Writer sees meta next to the quote). See tools/fakes.py for the expected shape.
    return not_implemented("appreviews")


@function_tool
async def review_stats(ctx: RunContextWrapper[Research], appid: int, start: str, end: str, group_by: str = "language") -> dict:
    """Positive/negative counts and negative share per group, for reviews between start and end.

    Args:
        appid: The Steam app id.
        start: First day, YYYY-MM-DD.
        end: Last day, YYYY-MM-DD.
        group_by: "language" or "playtime".

    Returns {"ok", "group_by", "groups": [{group, up, down, pct_negative}], "sampled": int}.
    """
    # TODO(A): group the sampled reviews; playtime buckets e.g. <10h, 10-100h, >100h.
    return not_implemented("appreviews")
