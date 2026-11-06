
import asyncio

from tools.steam_reviews import fetch_review_pages, review_snapshot_name
from tools.http import save_snapshot, close_client


async def main():
    appid = 1086940

    params = {
        "appid": appid,
        "start": "2026-10-01",
        "end": "2026-10-31",
        "review_type": "negative",
        "language": "english"
    }

    try:
        # Fetch real Steam reviews
        result = await fetch_review_pages(
            **params,
            max_pages=3
        )

        # Save the response and its query parameters
        path = save_snapshot(
            appid,
            # Named after the query, so sample_reviews finds it (scripts/make_snapshot.py does this for any window)
            review_snapshot_name(params["review_type"], params["language"], params["start"], params["end"]),
            {
                "params": params,
                "data": result
            }
        )

        print("Saved:", path)
        print("Reviews:", len(result["reviews"]))
        print("Total matching:", result["total_matching"])
        print("Pages fetched:", result["pages_fetched"])
        print("Partial sample:", result["sampled"])

    finally:
        await close_client()


if __name__ == "__main__":
    asyncio.run(main())
