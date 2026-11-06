import asyncio
import json
import pytest

from agents.tool_context import ToolContext
from detective.hooks import TraceHooks
from detective.state import Research
from tools import http, steam_reviews
from datetime import datetime, timezone

from tools.steam_reviews import (
    calculate_review_trend,
    calculate_review_stats,
)


def test_review_trend_detects_spike():
    """Verify that a significant negative review spike is detected."""

    dates = [
        "2026-01-01",
        "2026-02-01",
        "2026-03-01",
        "2026-04-01",
        "2026-05-01",
    ]

    rollups = []

    for i, date in enumerate(dates):
        timestamp = int(
            datetime.fromisoformat(date)
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )

        rollups.append({
            "date": timestamp,
            "recommendations_up": 40 if i == 4 else 90,
            "recommendations_down": 60 if i == 4 else 10,
        })

    data = {
        "results": {
            "rollup_type": "month",
            "rollups": rollups,
        }
    }

    result = calculate_review_trend(data)

    assert len(result["series"]) == 5
    assert len(result["spikes"]) == 1
    assert result["spikes"][0]["period"] == "2026-05-01"
    assert result["spikes"][0]["pct_negative"] == 60.0
    assert result["spikes"][0]["baseline"] == 10.0


def test_review_stats_language():
    """Verify language grouping and negative review percentages."""

    reviews = [
        {"language": "english", "voted_up": True},
        {"language": "english", "voted_up": False},
        {"language": "schinese", "voted_up": True},
    ]

    result = calculate_review_stats(reviews, "language")
    groups = {item["group"]: item for item in result}

    assert groups["english"]["up"] == 1
    assert groups["english"]["down"] == 1
    assert groups["english"]["pct_negative"] == 50.0

    assert groups["schinese"]["up"] == 1
    assert groups["schinese"]["pct_negative"] == 0.0


def test_review_stats_playtime_boundaries():
    """Verify playtime group boundaries in minutes."""

    reviews = [
        {"voted_up": False, "author": {"playtime_at_review": 599}},
        {"voted_up": True, "author": {"playtime_at_review": 600}},
        {"voted_up": True, "author": {"playtime_at_review": 5999}},
        {"voted_up": False, "author": {"playtime_at_review": 6000}},
    ]

    result = calculate_review_stats(reviews, "playtime")
    groups = {item["group"]: item for item in result}

    assert groups["<10h"]["down"] == 1
    assert groups["10-<100h"]["up"] == 2
    assert groups[">=100h"]["down"] == 1


def test_review_trend_snapshot_fallback(monkeypatch, tmp_path):
    """Verify snapshot fallback when the live Steam API fails."""

    # Use a temporary snapshot directory for an isolated test
    monkeypatch.setattr(http, "SNAPSHOT_DIR", tmp_path)

    appid = 1086940
    snapshot_date = "2026-11-05"

    timestamp = int(
        datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()
    )

    # Create a small valid snapshot
    http.save_snapshot(
        appid,
        "review_histogram_all",
        {
            "as_of": snapshot_date,
            "params": {
                "appid": appid,
                "language": "all"
            },
            "data": {
                "success": 1,
                "results": {
                    "rollup_type": "month",
                    "rollups": [
                        {
                            "date": timestamp,
                            "recommendations_up": 90,
                            "recommendations_down": 10
                        }
                    ]
                }
            }
        }
    )

    # Simulate a failed Steam API request
    async def failed_get_json(*args, **kwargs):
        raise RuntimeError("Simulated Steam API failure")

    monkeypatch.setattr(
        steam_reviews,
        "get_json",
        failed_get_json
    )

    state = Research(hooks=TraceHooks())
    args = {"appid": appid, "language": "all"}

    ctx = ToolContext(
        context=state,
        tool_name="get_review_trend",
        tool_call_id="snapshot_test",
        tool_arguments=json.dumps(args)
    )

    # Execute the real tool using the fallback snapshot
    result = asyncio.run(
        steam_reviews.get_review_trend.on_invoke_tool(
            ctx,
            json.dumps(args)
        )
    )

    assert result["ok"] is True
    assert result["from_snapshot"] is True
    assert result["as_of"] == snapshot_date
    assert result["granularity"] == "month"
    assert len(result["series"]) == 1
    assert result["series"][0]["pct_negative"] == 10.0
    assert len(state.store) == 1
    assert state.chart is not None


def test_sample_reviews_snapshot_fallback(monkeypatch, tmp_path):
    """Verify review snapshot fallback when the Steam API fails."""

    # Use an isolated snapshot directory
    monkeypatch.setattr(http, "SNAPSHOT_DIR", tmp_path)

    appid = 1086940
    snapshot_date = "2026-11-06"

    query = {
        "appid": appid,
        "start": "2026-10-01",
        "end": "2026-10-31",
        "review_type": "negative",
        "language": "english"
    }

    timestamp = int(
        datetime(2026, 10, 15, tzinfo=timezone.utc).timestamp()
    )

    # Create a sample Steam review
    review = {
        "recommendationid": "test-review-001",
        "author": {
            "steamid": "76561198000000000",
            "playtime_at_review": 9000
        },
        "language": "english",
        "review": "The latest update caused performance issues.",
        "timestamp_created": timestamp,
        "voted_up": False,
        "weighted_vote_score": 0.9,
        "votes_up": 10
    }

    # Save a matching snapshot
    http.save_snapshot(
        appid,
        "reviews_negative_english_2026_10",
        {
            "as_of": snapshot_date,
            "params": query,
            "data": {
                "reviews": [review],
                "total_matching": 1,
                "pages_fetched": 1,
                "sampled": False
            }
        }
    )

    # Simulate a failed live Steam request
    async def failed_fetch(*args, **kwargs):
        raise RuntimeError("Simulated Steam API failure")

    monkeypatch.setattr(
        steam_reviews,
        "fetch_review_pages",
        failed_fetch
    )

    state = Research(hooks=TraceHooks())

    args = {
        **query,
        "min_hours": 100,
        "max_hours": None,
        "limit": 5
    }

    ctx = ToolContext(
        context=state,
        tool_name="sample_reviews",
        tool_call_id="snapshot_review_test",
        tool_arguments=json.dumps(args)
    )

    # Execute the tool using the saved snapshot
    result = asyncio.run(
        steam_reviews.sample_reviews.on_invoke_tool(
            ctx,
            json.dumps(args)
        )
    )

    assert result["ok"] is True
    assert result["from_snapshot"] is True
    assert result["as_of"] == snapshot_date

    assert len(result["reviews"]) == 1
    assert len(state.store) == 1

    returned_review = result["reviews"][0]

    assert returned_review["positive"] is False
    assert returned_review["language"] == "english"
    assert returned_review["hours_at_review"] == 150.0

    # Verify that the evidence reference is registered
    assert state.store.get(returned_review["ref"]) is not None



@pytest.mark.parametrize(
    "group_by, expected_groups",
    [
        ("language", {"english", "schinese"}),
        ("playtime", {"10-<100h", ">=100h"})
    ]
)
def test_review_stats_snapshot_fallback(
    monkeypatch, tmp_path, group_by, expected_groups
):
    """Verify review statistics fallback for both grouping modes."""

    # Use an isolated snapshot directory
    monkeypatch.setattr(http, "SNAPSHOT_DIR", tmp_path)

    appid = 1086940
    start = "2026-10-01"
    end = "2026-10-31"
    snapshot_date = "2026-11-06"

    # Create sample reviews for both languages
    english_review = {
        "language": "english",
        "voted_up": True,
        "author": {"playtime_at_review": 600}
    }

    chinese_review = {
        "language": "schinese",
        "voted_up": False,
        "author": {"playtime_at_review": 7200}
    }

    review_sets = {
        "english": [english_review],
        "schinese": [chinese_review],
        "all": [english_review, chinese_review]
    }

    # Save snapshots with matching query parameters
    for language, reviews in review_sets.items():
        query = {
            "appid": appid,
            "start": start,
            "end": end,
            "review_type": "all",
            "language": language
        }

        http.save_snapshot(
            appid,
            f"review_stats_{language}_2026_10",
            {
                "as_of": snapshot_date,
                "params": query,
                "data": {
                    "reviews": reviews,
                    "total_matching": len(reviews),
                    "pages_fetched": 1,
                    "sampled": False
                }
            }
        )

    # Simulate a failed Steam API request
    async def failed_fetch(*args, **kwargs):
        raise RuntimeError("Simulated Steam API failure")

    monkeypatch.setattr(
        steam_reviews,
        "fetch_review_pages",
        failed_fetch
    )

    state = Research(hooks=TraceHooks())

    args = {
        "appid": appid,
        "start": start,
        "end": end,
        "group_by": group_by
    }

    ctx = ToolContext(
        context=state,
        tool_name="review_stats",
        tool_call_id=f"snapshot_{group_by}",
        tool_arguments=json.dumps(args)
    )

    # Execute the real tool with the simulated API failure
    result = asyncio.run(
        steam_reviews.review_stats.on_invoke_tool(
            ctx,
            json.dumps(args)
        )
    )

    # Verify successful snapshot fallback
    assert result["ok"] is True
    assert result["from_snapshot"] is True
    assert result["as_of"] == snapshot_date
    assert result["sampled"] == 2
    assert result["partial_sample"] is False

    # Verify statistics and evidence references
    groups = result["groups"]

    assert {item["group"] for item in groups} == expected_groups
    assert sum(item["up"] + item["down"] for item in groups) == 2

    assert len(state.store) == len(groups)
    assert all(
        state.store.get(item["ref"]) is not None
        for item in groups
    )
