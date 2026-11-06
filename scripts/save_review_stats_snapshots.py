
import asyncio

from tools.steam_reviews import fetch_review_pages
from tools.http import save_snapshot, close_client


async def main():
    appid = 1086940
    start = "2026-10-01"
    end = "2026-10-31"

    try:
        # Save separate snapshots for language and playtime comparisons
        for language in ("english", "schinese", "all"):
            query = {
                "appid": appid,
                "start": start,
                "end": end,
                "review_type": "all",
                "language": language
            }

            # Fetch real reviews from Steam
            result = await fetch_review_pages(
                **query,
                max_pages=3
            )

            # Save the response with its exact query parameters
            name = f"review_stats_{language}_2026_10"

            path = save_snapshot(
                appid,
                name,
                {
                    "params": query,
                    "data": result
                }
            )

            print(f"\nLanguage: {language}")
            print("Saved:", path)
            print("Reviews:", len(result["reviews"]))
            print("Total matching:", result["total_matching"])
            print("Pages fetched:", result["pages_fetched"])
            print("Partial sample:", result["sampled"])

    finally:
        await close_client()


if __name__ == "__main__":
    asyncio.run(main())
