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

import json
from agents import RunContextWrapper, function_tool

from detective.state import Research
from tools import http as _http
from tools.http import get_json, error, load_snapshot, not_implemented
from datetime import datetime, timezone

# Review sample snapshots (written by scripts/make_snapshot.py, read by sample_reviews as a fallback).
LEGACY_REVIEW_SNAPSHOTS = ("reviews_negative_english_2026_10",)  # saved before names carried the query


def review_snapshot_name(review_type: str, language: str, start: str, end: str) -> str:
    """Snapshot name for one sample_reviews query, e.g. reviews_negative_english_2026-08-01_2026-10-31."""
    clean = lambda v: "".join(c if c.isalnum() or c in "-" else "-" for c in str(v))
    return f"reviews_{clean(review_type)}_{clean(language)}_{clean(start)}_{clean(end)}"


def _review_day(review: dict) -> str:
    return datetime.fromtimestamp(review["timestamp_created"], tz=timezone.utc).strftime("%Y-%m-%d")


def _valid_review_snapshot(snapshot, appid: int) -> bool:
    params = (snapshot or {}).get("params") or {}
    data = (snapshot or {}).get("data")
    return (
        params.get("appid") == appid
        and all(isinstance(params.get(k), str) for k in ("start", "end", "review_type", "language"))
        and isinstance(data, dict)
        and isinstance(data.get("reviews"), list)
        and isinstance(data.get("sampled"), bool)
        and isinstance(data.get("pages_fetched"), int)
        and isinstance(snapshot.get("as_of"), str)
    )


def find_review_snapshot(appid: int, start: str, end: str, review_type: str, language: str) -> dict | None:
    """The best saved review sample for this query, already narrowed to it, or None.

    Order: the exact query's snapshot (or a legacy one with identical params); then any review
    snapshot for this appid that is at least as broad (review_type "all" or the same; language "all"
    or the same) and whose window overlaps the request, preferring full coverage, then more overlap.
    Returns {"as_of", "data": {reviews, total_matching, pages_fetched, sampled}, "window": [s, e],
    "exact": bool}; "window" is the part of the request the snapshot covers.
    """
    query = {"appid": appid, "start": start, "end": end, "review_type": review_type, "language": language}
    for name in (review_snapshot_name(review_type, language, start, end), *LEGACY_REVIEW_SNAPSHOTS):
        snapshot = load_snapshot(appid, name)
        if _valid_review_snapshot(snapshot, appid) and snapshot["params"] == query:
            return {"as_of": snapshot["as_of"], "data": snapshot["data"], "window": [start, end], "exact": True}

    candidates = []
    folder = _http.SNAPSHOT_DIR / str(appid)
    for path in sorted(folder.glob("reviews_*.json")) if folder.is_dir() else []:
        snapshot = load_snapshot(appid, path.stem)
        if not _valid_review_snapshot(snapshot, appid):
            continue
        params = snapshot["params"]
        if params["review_type"] not in ("all", review_type) or params["language"] not in ("all", language):
            continue
        lo, hi = max(start, params["start"]), min(end, params["end"])
        if lo > hi:
            continue
        reviews = []
        for review in snapshot["data"]["reviews"]:
            try:
                day = _review_day(review)
            except (KeyError, TypeError, ValueError, OSError):
                continue
            if not lo <= day <= hi:
                continue
            if review_type != "all" and bool(review.get("voted_up")) != (review_type == "positive"):
                continue
            if language != "all" and review.get("language") != language:
                continue
            reviews.append(review)
        covers = params["start"] <= start and end <= params["end"]
        # Filtering a complete snapshot that covers the whole request is still complete; a sampled
        # snapshot, or one that covers only part of the request, gives a sample.
        complete = covers and not snapshot["data"]["sampled"]
        candidates.append((covers, hi, -len(reviews), {
            "as_of": snapshot["as_of"],
            "data": {
                "reviews": reviews,
                "total_matching": len(reviews) if complete else None,
                "pages_fetched": snapshot["data"]["pages_fetched"],
                "sampled": not complete,
            },
            "window": [lo, hi],
            "exact": False,
        }))
    if not candidates:
        return None
    # Full coverage first, then the latest-ending overlap, then the most reviews.
    candidates.sort(key=lambda c: (c[0], c[1], -c[2]), reverse=True)
    return candidates[0][3]


def calculate_review_trend(data: dict) -> dict:
    """Calculate review trends and detect negative review spikes."""

    results = data["results"]
    rollups = sorted(
        results["rollups"],
        key=lambda item: item["date"]
    )

    series = []
    spikes = []

    # Calculate review statistics for each period
    for item in rollups:
        up = item["recommendations_up"]
        down = item["recommendations_down"]
        total = up + down

        if total == 0:
            continue

        date = datetime.fromtimestamp(
            item["date"], tz=timezone.utc
        ).strftime("%Y-%m-%d")

        pct_negative = round(down / total * 100, 1)

        series.append({
            "period": date,
            "up": up,
            "down": down,
            "pct_negative": pct_negative
        })

    # Detect spikes using the previous four periods
    for i in range(4, len(series)):
        previous_four = series[i - 4:i]

        baseline = sum(
            item["pct_negative"] for item in previous_four
        ) / 4

        current = series[i]

        if baseline > 0 and current["pct_negative"] > 2 * baseline:
            spikes.append({
                "period": current["period"],
                "pct_negative": current["pct_negative"],
                "baseline": round(baseline, 1)
            })

    return {
        "granularity": results["rollup_type"],
        "series": series,
        "spikes": spikes
    }

async def fetch_review_page(
    appid: int,
    start: str,
    end: str,
    review_type: str = "negative",
    language: str = "all",
    cursor: str = "*",
) -> dict:
    """Fetch one page of Steam reviews within a UTC date range."""

    review_types = {
        "all": 0,
        "positive": 1,
        "negative": 2
    }

    # Convert date strings to Unix timestamps
    start_date = datetime.strptime(
        start, "%Y-%m-%d"
    ).replace(tzinfo=timezone.utc)

    end_date = datetime.strptime(
        end, "%Y-%m-%d"
    ).replace(tzinfo=timezone.utc)

    if start_date > end_date:
        raise ValueError("Start date must not be after end date.")

    # Build the Steam API request
    payload = {
        "appid": appid,
        "filter": 1,
        "review_type": review_types[review_type],
        "purchase_type": 1,
        "languages": [language],
        "num_per_page": 100,
        "cursor": cursor,
        "date_range_start": int(start_date.timestamp()),
        "date_range_end": int(end_date.timestamp()) + 86399,
        "filter_offtopic_activity": False
    }

    # Fetch reviews through the shared HTTP client
    data = await get_json(
        "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/",
        params={"input_json": json.dumps(payload)},
        host_limit="steam_api"
    )

    return data["response"]

async def fetch_review_pages(
    appid: int,
    start: str,
    end: str,
    review_type: str = "negative",
    language: str = "all",
    max_pages: int = 3,
) -> dict:
    """Fetch multiple pages of Steam reviews with a bounded page limit."""

    if max_pages < 1:
        raise ValueError("max_pages must be at least 1.")

    reviews = []
    seen_ids = set()
    seen_cursors = set()
    cursor = "*"
    total_matching = None
    pages_fetched = 0

    # Fetch review pages until the limit or end of results
    for _ in range(max_pages):
        if cursor in seen_cursors:
            break

        seen_cursors.add(cursor)

        response = await fetch_review_page(
            appid=appid,
            start=start,
            end=end,
            review_type=review_type,
            language=language,
            cursor=cursor
        )

        pages_fetched += 1
        total_matching = response.get(
            "total_matching", total_matching
        )

        page_reviews = response.get("reviews", [])

        if not page_reviews:
            break

        # Avoid duplicate reviews across pages
        for review in page_reviews:
            review_id = review["recommendationid"]

            if review_id not in seen_ids:
                seen_ids.add(review_id)
                reviews.append(review)

        # Stop when all matching reviews have been collected
        if (
            isinstance(total_matching, int)
            and len(reviews) >= total_matching
        ):
            break

        next_cursor = response.get("cursor")

        if not next_cursor or next_cursor == cursor:
            break

        cursor = next_cursor

    return {
        "reviews": reviews,
        "total_matching": total_matching,
        "pages_fetched": pages_fetched,
        "sampled": (
            total_matching is None
            or len(reviews) < total_matching
        )
    }

def calculate_review_stats(
    reviews: list[dict],
    group_by: str = "language"
) -> list[dict]:
    """Group reviews by language or playtime and calculate negative rates."""

    if group_by not in ("language", "playtime"):
        raise ValueError("group_by must be 'language' or 'playtime'.")

    groups = {}

    # Assign each review to a group
    for review in reviews:
        positive = review.get("voted_up")

        if not isinstance(positive, bool):
            continue

        if group_by == "language":
            group = review.get("language", "unknown")
        else:
            minutes = review.get("author", {}).get("playtime_at_review")

            if minutes is None:
                group = "unknown"
            elif minutes < 600:
                group = "<10h"
            elif minutes < 6000:
                group = "10-<100h"
            else:
                group = ">=100h"

        if group not in groups:
            groups[group] = {
                "group": group,
                "up": 0,
                "down": 0
            }

        if positive:
            groups[group]["up"] += 1
        else:
            groups[group]["down"] += 1

    # Calculate the negative review rate for each group
    for item in groups.values():
        total = item["up"] + item["down"]
        item["pct_negative"] = round(
            item["down"] / total * 100, 1
        ) if total else 0

    return sorted(groups.values(), key=lambda item: item["group"])


def load_review_stats_snapshot(
    appid: int,
    start: str,
    end: str,
    language: str
) -> dict | None:
    """Load a review statistics snapshot only if its query matches."""

    # Build the snapshot filename from language and month
    month = start[:7].replace("-", "_")
    name = f"review_stats_{language}_{month}"

    snapshot = load_snapshot(appid, name)

    if snapshot is None:
        return None

    # Require an exact match with the original query
    expected_params = {
        "appid": appid,
        "start": start,
        "end": end,
        "review_type": "all",
        "language": language
    }

    if snapshot.get("params") != expected_params:
        return None

    data = snapshot.get("data")

    # Validate the saved review data
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("reviews"), list)
        or not isinstance(data.get("pages_fetched"), int)
        or not isinstance(data.get("sampled"), bool)
        or not isinstance(snapshot.get("as_of"), str)
    ):
        return None

    return {
        "data": data,
        "as_of": snapshot["as_of"]
    }

@function_tool
async def get_review_trend(ctx: RunContextWrapper[Research], appid: int, language: str = "all") -> dict:
    """Weekly or monthly positive/negative review counts for a game, and the periods where the
    negative share jumped.

    Args:
        appid: The Steam app id.
        language: Only "all" is supported: the histogram counts every language together. For a
            language comparison, use review_stats with group_by="language".

    Returns {"ok", "appid", "granularity", "series": [{period, up, down, pct_negative}],
    "spikes": [{period, pct_negative, baseline}], "source", "as_of", "from_snapshot"}.
    """

    source = f"https://store.steampowered.com/appreviewhistogram/{appid}"

    # The histogram does not guarantee language-specific review counts.
    if language != "all":
        return error(
            "unsupported_language",
            "Language-specific trends are not supported by this endpoint.",
            source
        )

    try:

        # Prefer live Steam data and fall back to a saved snapshot
        from_snapshot = False
        as_of = datetime.now(timezone.utc).date().isoformat()

        try:
            data = await get_json(
                source,
                params={
                    "l": "english",
                    "review_score_preference": 0
                },
                host_limit="steam_store"
            )

            if data.get("success") != 1:
                raise ValueError("Steam returned an unsuccessful response.")

        except Exception as live_error:
            # Use the saved histogram only when the live request fails
            snapshot = load_snapshot(appid, "review_histogram_all")

            if snapshot is None:
                return error(
                    "fetch_failed",
                    f"Live request failed and no snapshot exists: {live_error}",
                    source
                )

            if snapshot.get("params") != {
                "appid": appid,
                "language": language
            }:
                return error(
                    "invalid_snapshot",
                    "Snapshot parameters do not match the request.",
                    source
                )

            data = snapshot.get("data")

            if not isinstance(data, dict) or data.get("success") != 1:
                return error(
                    "invalid_snapshot",
                    "Saved histogram data is invalid.",
                    source
                )

            from_snapshot = True
            as_of = snapshot["as_of"]

        # Calculate review statistics and detect negative review spikes
        trend = calculate_review_trend(data)

        if not trend["series"]:
            return error("no_data", "No review trend data available.", source)

        # Register each historical period in the evidence store
        refs = {}

        for item in trend["series"]:
            period = item["period"]

            ref = ctx.context.store.add(
                "review_trend",
                f"{appid}:{period}",
                f"https://store.steampowered.com/app/{appid}/",
                period,
                (
                    f"Period {period}: "
                    f"{item['up']} positive reviews, "
                    f"{item['down']} negative reviews, "
                    f"{item['pct_negative']}% negative."
                )
            )

            item["ref"] = ref
            refs[period] = ref

        # Connect detected spikes to their supporting evidence
        for spike in trend["spikes"]:
            spike["ref"] = refs[spike["period"]]

        # Provide the review timeline to the frontend
        previous_chart = ctx.context.chart or {}

        ctx.context.chart = {
            "series": trend["series"],
            "patches": previous_chart.get("patches", [])
        }

        return {
            "ok": True,
            "appid": appid,
            "language": language,
            "granularity": trend["granularity"],
            "series": trend["series"],
            "spikes": trend["spikes"],
            "source": source,
            "as_of": as_of,
            "from_snapshot": from_snapshot
        }

    except Exception as exc:
        return error("fetch_failed", str(exc), source)


@function_tool
async def sample_reviews(
    ctx: RunContextWrapper[Research],
    appid: int,
    start: str,
    end: str,
    review_type: str = "all",  # was "negative": a default should not decide the report's tone (Jiashu)
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

    source = "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"

    # Validate filtering arguments
    if review_type not in ("all", "positive", "negative"):
        return error("invalid_argument", "Invalid review_type.", source)

    if not 1 <= limit <= 100:
        return error("invalid_argument", "limit must be between 1 and 100.", source)

    if (
        min_hours is not None
        and max_hours is not None
        and min_hours > max_hours
    ):
        return error("invalid_argument", "min_hours exceeds max_hours.", source)

    try:

        # Prepare the query parameters for live and snapshot data
        query = {
            "appid": appid,
            "start": start,
            "end": end,
            "review_type": review_type,
            "language": language
        }

        from_snapshot = False
        snapshot_window = None
        as_of = datetime.now(timezone.utc).date().isoformat()

        try:
            # Prefer live Steam review data
            page_data = await fetch_review_pages(
                **query,
                max_pages=3
            )

        except Exception as live_error:
            # Fall back to a saved sample for this query, or a broader one that overlaps it
            snapshot = find_review_snapshot(appid, start, end, review_type, language)

            if snapshot is None:
                return error(
                    "fetch_failed",
                    f"Live request failed and no matching snapshot exists: {live_error}",
                    source
                )

            page_data = snapshot["data"]
            from_snapshot = True
            as_of = snapshot["as_of"]
            snapshot_window = snapshot["window"]

        # Filter reviews by playtime at the time of review
        filtered = []

        for review in page_data["reviews"]:
            author = review.get("author", {})
            hours = author.get("playtime_at_review", 0) / 60

            if min_hours is not None and hours < min_hours:
                continue

            if max_hours is not None and hours > max_hours:
                continue

            filtered.append((review, hours))

        # Rank reviews by usefulness
        filtered.sort(
            key=lambda item: (
                float(item[0].get("weighted_vote_score", 0)),
                int(item[0].get("votes_up", 0))
            ),
            reverse=True
        )

        selected = filtered[:limit]
        output_reviews = []

        # Register selected reviews as evidence
        for review, hours in selected:
            review_id = str(review["recommendationid"])
            steamid = review["author"]["steamid"]

            date = datetime.fromtimestamp(
                review["timestamp_created"],
                tz=timezone.utc
            ).strftime("%Y-%m-%d")

            text = review.get("review", "")
            positive = review["voted_up"]
            review_language = review["language"]

            url = (
                f"https://steamcommunity.com/profiles/"
                f"{steamid}/recommended/{appid}/"
            )

            ref = ctx.context.store.add(
                "review",
                review_id,
                url,
                date,
                text,
                meta={
                    "language": review_language,
                    "hours_at_review": round(hours, 1),
                    "positive": positive
                }
            )

            output_reviews.append({
                "ref": ref,
                "date": date,
                "url": url,
                "language": review_language,
                "positive": positive,
                "hours_at_review": round(hours, 1),
                "text": text[:400]
            })

        sampled = (
            page_data["sampled"]
            or len(filtered) > limit
        )

        return {
            "ok": True,
            "appid": appid,
            "total_in_range": (
                len(filtered) if not page_data["sampled"] else None
            ),
            "sampled_in_range": len(filtered),
            "total_matching": page_data["total_matching"],
            "pages_fetched": page_data["pages_fetched"],
            "sampled": sampled,
            "reviews": output_reviews,
            "source": source,
            "as_of": as_of,
            "from_snapshot": from_snapshot,
            # With a snapshot, the part of the requested window the saved sample covers
            **({"snapshot_window": snapshot_window} if from_snapshot else {})
        }

    except ValueError as exc:
        return error("invalid_argument", str(exc), source)

    except Exception as exc:
        return error("fetch_failed", str(exc), source)

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

    source = "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"

    # Validate the grouping argument
    if group_by not in ("language", "playtime"):
        return error(
            "invalid_argument",
            "group_by must be 'language' or 'playtime'.",
            source
        )

    try:

        # Track the data source and capture date
        from_snapshot = False
        as_of = datetime.now(timezone.utc).date().isoformat()
        language_coverage = None

        if group_by == "language":
            # Fetch English and Chinese reviews separately
            pages = {}

            try:
                for lang in ("english", "schinese"):
                    pages[lang] = await fetch_review_pages(
                        appid=appid,
                        start=start,
                        end=end,
                        review_type="all",
                        language=lang,
                        max_pages=3
                    )

            except Exception as live_error:
                # Fall back to both snapshots for a consistent comparison
                snapshot_dates = []

                for lang in ("english", "schinese"):
                    snapshot = load_review_stats_snapshot(
                        appid, start, end, lang
                    )

                    if snapshot is None:
                        return error(
                            "fetch_failed",
                            f"No matching {lang} snapshot: {live_error}",
                            source
                        )

                    pages[lang] = snapshot["data"]
                    snapshot_dates.append(snapshot["as_of"])

                from_snapshot = True
                as_of = min(snapshot_dates)

            reviews = (
                pages["english"]["reviews"]
                + pages["schinese"]["reviews"]
            )

            language_coverage = {}

            for lang, page in pages.items():
                language_coverage[lang] = {
                    "sampled": len(page["reviews"]),
                    "total_matching": page["total_matching"],
                    "pages_fetched": page["pages_fetched"],
                    "partial_sample": page["sampled"]
                }

            totals = [
                page["total_matching"]
                for page in pages.values()
            ]

            total_matching = (
                sum(totals)
                if all(isinstance(n, int) for n in totals)
                else None
            )

            pages_fetched = sum(
                page["pages_fetched"]
                for page in pages.values()
            )

            partial_sample = any(
                page["sampled"]
                for page in pages.values()
            )

        else:
            # Fetch reviews from all languages for playtime grouping
            try:
                page_data = await fetch_review_pages(
                    appid=appid,
                    start=start,
                    end=end,
                    review_type="all",
                    language="all",
                    max_pages=3
                )

            except Exception as live_error:
                # Fall back to the combined review snapshot
                snapshot = load_review_stats_snapshot(
                    appid, start, end, "all"
                )

                if snapshot is None:
                    return error(
                        "fetch_failed",
                        f"No matching review snapshot: {live_error}",
                        source
                    )

                page_data = snapshot["data"]
                from_snapshot = True
                as_of = snapshot["as_of"]

            reviews = page_data["reviews"]
            total_matching = page_data["total_matching"]
            pages_fetched = page_data["pages_fetched"]
            partial_sample = page_data["sampled"]



        # Calculate review counts and negative rates by group
        groups = calculate_review_stats(
            reviews,
            group_by=group_by
        )

        # Register each group's statistics as evidence
        for item in groups:
            group = item["group"]
            count = item["up"] + item["down"]

            evidence_text = (
                f"Review statistics for {group_by} group '{group}' "
                f"from {start} to {end}: "
                f"{count} observed reviews, "
                f"{item['up']} positive, "
                f"{item['down']} negative, "
                f"{item['pct_negative']}% negative. "
                f"Partial sample: {partial_sample}."
            )

            ref = ctx.context.store.add(
                "review_stats",
                f"{appid}:{start}:{end}:{group_by}:{group}",
                f"https://store.steampowered.com/app/{appid}/",
                end,
                evidence_text,
                meta={
                    "group_by": group_by,
                    "group": group,
                    "count": count,
                    "partial_sample": partial_sample
                }
            )

            item["ref"] = ref

        return {
            "ok": True,
            "appid": appid,
            "group_by": group_by,
            "start": start,
            "end": end,
            "groups": groups,

            "sampled": len(reviews),
            "partial_sample": partial_sample,
            "total_matching": total_matching,
            "pages_fetched": pages_fetched,
            "language_coverage": language_coverage,
            "source": source,
            "as_of": as_of,
            "from_snapshot": from_snapshot
        }

    except ValueError as exc:
        return error("invalid_argument", str(exc), source)

    except Exception as exc:
        return error("fetch_failed", str(exc), source)
