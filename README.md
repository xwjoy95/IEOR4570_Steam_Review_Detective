# Steam Review Detective

## Overview

Name a Steam game and ask why its reviews changed. A Lead agent splits the question, three
researchers check player reviews, patch notes and player counts in parallel, and a Writer turns
their findings into a report where every fact cites a review, announcement or data point `[R12]`.

Status: all three researchers use real data tools by default: Steam reviews, store details and
announcements, and player statistics. The shared HTTP layer provides caching, rate limits and retries;
each tool can fall back to a compatible snapshot. Offline tests cover the tools and orchestration.

## Target users

- Players deciding whether to buy or return to a game: the store page says "Mixed", but not whether
  the bad reviews are old news or a new problem.
- Indie developers and publishers: which competitor update backfired, and what players care about.

A model alone can't answer this: reviews change daily, and recent reviews and patches aren't in its
training data.

## Architecture

```
app.py                 FastAPI: /, POST /chat, GET /run (SSE), in-memory sessions
evidence.py            EvidenceStore: numbers every citable item (R1, R2...) and validates citations
detective/             the agents (not named `agents/`: that would shadow the OpenAI Agents SDK)
  llm.py               Gemini via LiteLLM: SMART (flash) and CHEAP (flash-lite), shared settings
  orchestrator.py      deep_research(): clarify -> research (<=3 rounds) -> write -> check citations
  lead.py              Clarifier (asks back when vague) and Lead (splits the question, fans out, finds gaps)
  researchers.py       3 researchers' prompts + tool subsets, and the `research` fan-out tool
  writer.py            Writer + citation check (existence, then grounding of numbers and quotes)
  state.py             Research: one run's state (hooks, evidence store, appid, findings)
  hooks.py             TraceHooks: records tool calls for /chat, feeds the context panel
tools/                 data tools, one module per teammate
  http.py              shared async client, cache, rate limits, snapshot fallback   (B)
  steam_reviews.py     get_review_trend, sample_reviews, review_stats              (A)
  steam_news.py        search_game, get_patch_notes, get_game_info                 (B)
  player_stats.py      get_player_stats, get_player_history                        (C)
context_panel.py / context-panel.js   context panel, copied from the course reference
static/index.html      the page
scripts/make_snapshot.py              saves demo games' data to data/snapshots/<appid>/  (C)
tests/                 pytest
```

## Data sources

| Source | Endpoint | Used for |
|---|---|---|
| Steam reviews | `store.steampowered.com/appreviews/<appid>`, `appreviewhistogram/<appid>` | review text, trend, language and playtime splits |
| Steam news + store | `ISteamNews/GetNewsForApp/v2`, `store/api/appdetails`, `store/api/storesearch` | patch notes, game info, name -> appid |
| Player counts | SteamSpy `api.php?request=appdetails`, `ISteamUserStats/GetNumberOfCurrentPlayers`, SteamCharts | owner estimates, current and historical players |

Snapshots of the three demo games live in `data/snapshots/<appid>/` so the demo works when an API
is down or rate-limited. All three use the same window, **2026-08-01 to 2026-10-31**:

| Appid | Game | Why it's a demo game |
|---|---|---|
| 553850 | Helldivers 2 | negative reviews jumped from ~13% to 63% in October 2026 |
| 1086940 | Baldur's Gate 3 | a stable baseline (~5% negative) with large English and Chinese review volume |
| 578080 | PUBG | a September spike to 78% negative that partially recovered |

Each folder holds every source (`appdetails`, `news_official`, `review_histogram_all`, review
samples and per-month stats, `steamspy`, `steamcharts`) with an `as_of` collection date. Tools try
the live endpoint first and fall back to the snapshot when it fails or the window is incomplete,
telling the model (and the report) which happened. To regenerate:

```bash
uv run scripts/make_snapshot.py 553850 578080 1086940 --start 2026-08-01 --end 2026-10-31
```

## Setup

```bash
cp .env.example .env
gcloud auth application-default login
uv sync
uv run app.py          # http://localhost:8000
uv run pytest                 # offline: no model calls, no network
uv run scripts/e2e_runs.py    # the README's sample queries (plus a vague and a follow-up demo) with
                              # Gemini; saves responses to docs/sample_runs/
uv run scripts/baseline.py    # the same model with no tools and no system prompt -> docs/baselines/
uv run scripts/verify_online.py  # one-command online check (steam, vertex, sample queries,
                                 # follow-up, vague, SSE stream); PASS/FAIL per check, ~10-15 min
```

For local live research, authenticate with `gcloud auth application-default login` and configure
`GOOGLE_CLOUD_PROJECT` in your local `.env`, pass `--gcp-project`, or keep the project ID in a local
`gcp-project.txt` beside the repository. Then run:

```bash
uv run app.py --host 127.0.0.1 --port 8000
```

Both the browser UI and `/chat` run the real LLM workflow. Bundled snapshots are data fallbacks,
not prewritten answers. `USE_FAKE_TOOLS=1` changes data tools only; it still requires model credentials.

## Response shape

`POST /chat` and the `/run` "done" event return the same fields, and both save the turn so
follow-up questions ("what about Chinese players?") work in the same session:

- `response` / `markdown`: the cited report (markdown).
- `tool_calls`: `[{agent, name, args, result}]`, one entry per tool call, grouped by agent.
- `evidence`: `{R12: {source, raw_id, url, date, text, meta}}` — what each citation opens.
- `chart`: the review timeline the page draws:
  `{appid, granularity, as_of, from_snapshot, series: [{period, up, down, pct_negative, ref}], patches: [{ref, id, date, title, url, official, kind}], players: [{month, avg, ref}]}`.
  `get_review_trend` fills `series`, `granularity` and the collection date `as_of`;
  `get_patch_notes` fills `patches` for the same game; a different game starts a new timeline;
  `build_chart` adds the `players` series from the researchers' player-history evidence, and
  derives `patches` from the evidence if no trend call happened.
- `games`: `[{appid, name, store_url, header_image, developer, release_date, genres, price}]` —
  the dossier header cards (facts only when known).
- `timeline`: `[{agent, start, end}]` in seconds from the run's start — shows the parallel fan-out.

`POST /clear?session_id=...` forgets one conversation (the page's delete button).

## Fake data and snapshots

The tools use real Steam data by default. `USE_FAKE_TOOLS=1` runs every tool on the fictional fake data in
`tools/fakes.py` ("Ashen Frontier", appid 2999990, no network), with the real tools' names and schemas.
Runtime tools read snapshots without changing tracked data. Generate snapshots explicitly:

```bash
uv run scripts/make_snapshot.py 553850 --start 2026-08-01 --end 2026-10-31
# Store details and official announcements only:
uv run scripts/make_snapshot.py 1086940 --start 2026-08-01 --end 2026-10-31 --only appdetails news_official
```

Store details use the US region and English localization. Prices describe collection time, not price
history. Announcement windows include both dates in UTC. A news snapshot must fully cover the requested
window and feed; official-only data cannot substitute for a query that also requests press coverage.
Snapshots keep raw IDs, while each run registers its own evidence references. Search fallback covers
only games with local detail snapshots. Test responses are stored under `tests/fixtures/`.

Keep credentials outside the repository or in a local ignored `.env`. Never put keys in snapshots,
sample responses, reports or frontend code. These three B endpoints do not use the Steam key.

## Deployment (Cloud Run)

Continuous deploy from GitHub, following the course's "Deploying to Cloud Run from GitHub":

1. Push the repo to GitHub (the `Dockerfile` at the root is the build definition; snapshots ship
   with the deploy, well under the 100 MB limit).
2. In Google Cloud Console: Cloud Build > Triggers > Create trigger, push event `main`, type
   "Deploy to Cloud Run", service `steam-review-detective`, region `us-central1`, and allow
   unauthenticated invocations.
3. Give the Cloud Run service account the Vertex AI User role, and set these environment variables
   on the service: `GOOGLE_CLOUD_PROJECT=<project-id>`.
4. Raise Cloud Run's request timeout to at least **300 s** (Console: service > Edit & Deploy New
   Revision > Request timeout). The agent's own budget is lower (`RESEARCH_TIMEOUT`, default 240 s)
   and researchers are capped at 3 rounds / 6 researchers / 6 tool calls each, so answers fit.

`submission.json` points at the service URL once the first deploy succeeds.

## Sample queries

All three run on the snapshot window 2026-08-01 to 2026-10-31 (collection dates in each snapshot's
`as_of`). Full traces: `docs/sample_runs/real_<name>.json` (agent) and `docs/baselines/<name>.md`
(the same model, no tools, no system prompt). The baseline cannot answer any of the three: the
model says its knowledge ends in January 2025.

1. **Which update caused Helldivers 2's negative reviews to spike between August and October 2026, and what exactly are players complaining about?**
   - Expected answer: the "Devoid of Liberty: 7.2.0" update (2026-10-20), which added Easy
     Anti-Cheat; the October rollup went to ~64% negative (6754 negative vs 3813 positive), and
     players blame constant crashes, broken mods and EAC being pointless for a PvE co-op game.
   - Sources: `data/snapshots/553850/review_histogram_all.json` (the spike),
     `news_official.json` (the update), `reviews_negative_all_2026-08-01_2026-10-31.json`
     (the complaints), `steamcharts.json` (player counts).
   - Agent (real_q1_spike, 89 s, 26 citations): names the update, the 63.9% October rollup, and
     the three complaint clusters, each cited to a review or announcement.
   - Same model, no tools: refuses (knowledge cutoff January 2025), then offers 2024's
     "Escalation of Freedom" episode instead of the 2026 answer.
2. **Do Chinese and English players complain about the same things in Baldur's Gate 3 between August and October 2026?**
   - Expected answer: both groups complain about combat RNG, miss rates and bugs, but English
     reviews focus on slow pacing and character writing while Chinese reviews (schinese +
     tchinese) focus on translation quality, controller UI and multiplayer connection issues.
   - Sources: `data/snapshots/1086940/reviews_negative_english_2026-08-01_2026-10-31.json`,
     `reviews_negative_schinese_2026-08-01_2026-10-31.json` (the two samples),
     `review_stats_*_2026_08/09/10.json` (group percentages), `steamcharts.json`.
   - Agent (real_q2_languages, 70 s, 20 citations): states the shared complaints and each group's
     distinctive ones, quotes reviews in their original languages, and adds the player-count trend.
   - Same model, no tools: refuses outright (no 2026 data).
3. **How do players with 100+ hours review PUBG between August and October 2026 differently from newcomers?**
   - Expected answer: veterans are more negative overall (~37% vs ~30% negative); both complain
     about cheating and performance, but veterans focus on slow developer updates, publisher
     decisions and the September esports ban controversy, while newcomers mention campers,
     monetization, and praise the gunplay.
   - Sources: `data/snapshots/578080/reviews_negative_all_2026-08-01_2026-10-31.json`
     (hours-at-review per review), `review_histogram_all.json` (the September spike to ~78%),
     `news_official.json`, `steamspy.json`.
   - Agent (real_q3_veterans, 134 s, 37 citations): gives the per-playtime-band percentages and
     the distinct complaint themes for veterans vs newcomers, all cited.
   - Same model, no tools: refuses (the timeframe doesn't exist for it), then invents a generic
     veteran-vs-newcomer projection with no data.

## Team split

| Who | Owns |
|---|---|
| A | Review Analyst: `tools/steam_reviews.py` |
| B | Patch Historian: `tools/steam_news.py`, `tools/http.py` |
| C | Player Data Analyst: `tools/player_stats.py`, `scripts/make_snapshot.py` |
| Jiashu | orchestration (`detective/`, `evidence.py`, `app.py`) and frontend |
