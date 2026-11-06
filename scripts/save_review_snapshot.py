
import asyncio

from tools.steam_reviews import fetch_review_pages
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
            "reviews_negative_english_2026_10",
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
