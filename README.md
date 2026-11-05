# Steam Review Detective

## Overview

Name a Steam game and ask why its reviews changed. A Lead agent splits the question, three
researchers check player reviews, patch notes and player counts in parallel, and a Writer turns
their findings into a report where every fact cites a review, announcement or data point `[R12]`.

Status: the agents work end to end on fake data; the real data tools are stubs (`TODO(A/B/C)` marks who owns what).

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
  writer.py            Writer + citation check
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

Snapshots of the demo games live in `data/snapshots/` so the demo works when an API is down.

## Setup

```bash
cp .env.example .env
gcloud auth application-default login
uv sync
uv run app.py          # http://localhost:8000
uv run pytest                 # offline: no model calls, no network
uv run scripts/e2e_runs.py    # 3 real runs with Gemini; saves responses to docs/sample_runs/
```

`USE_FAKE_TOOLS=1` (the default for now) runs every tool on the fictional fake data in
`tools/fakes.py` ("Ashen Frontier", appid 2999990), with the real tools' names and schemas. Set
`USE_FAKE_TOOLS=0` in `.env` once the real tools work.

## Sample queries

1. **Which update caused the recent spike in negative reviews for TODO, and what are players complaining about?**
   - Expected answer: TODO
   - Sources: TODO
   - Same model, no tools: TODO
2. **Do Chinese and English players complain about the same things in TODO?**
   - Expected answer: TODO
   - Sources: TODO
   - Same model, no tools: TODO
3. **How do players with 100+ hours review TODO differently from newcomers?**
   - Expected answer: TODO
   - Sources: TODO
   - Same model, no tools: TODO

## Team split

| Who | Owns |
|---|---|
| A | Review Analyst: `tools/steam_reviews.py` |
| B | Patch Historian: `tools/steam_news.py`, `tools/http.py` |
| C | Player Data Analyst: `tools/player_stats.py`, `scripts/make_snapshot.py` |
| Jiashu | orchestration (`detective/`, `evidence.py`, `app.py`) and frontend |
