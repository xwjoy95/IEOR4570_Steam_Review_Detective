"""FastAPI app: the page, POST /chat (Project 1's response shape), and GET /run (live events, as in
the course reference). Endpoints only: the agents live in detective/, the data tools in tools/."""

import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Steam Review Detective with live LLM research.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    parser.add_argument("--gcp-project", help="Google Cloud project for Vertex AI calls.")
    return parser.parse_args()


# Configure the project before the agents construct their model settings.
if __name__ == "__main__":
    args = parse_args()
    if args.gcp_project:
        os.environ["GOOGLE_CLOUD_PROJECT"] = args.gcp_project

from context_panel import AgentContexts, add_context_panel  # noqa: E402
from detective.hooks import TraceHooks  # noqa: E402
from detective.orchestrator import deep_research  # noqa: E402
from detective.state import Research  # noqa: E402
from tools import USE_FAKE_TOOLS, steam_news  # noqa: E402


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ToolCall(BaseModel):
    agent: str  # "Lead", "Review Analyst", "Writer", ...
    name: str
    args: dict
    result: str  # truncated; the full data is in evidence


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[ToolCall] = []
    evidence: dict = {}  # "R12" -> {source, raw_id, url, date, text}: the page opens citations from it
    chart: dict | None = None  # timeline data: review trend + patch dates
    games: list[dict] = []  # [{appid, name}] the question was about (empty when the Clarifier asked back)
    timeline: list[dict] = []  # [{agent, start, end}] seconds since the run began: who worked when


# In-memory, single process.
history: dict[str, list[dict]] = {}  # session_id -> [{role, content}], for follow-up questions
last_games: dict[str, list[dict]] = {}  # session_id -> the games the last question was about
sessions: dict[str, AgentContexts] = {}  # run_id -> every agent's context, for the context panel

app = FastAPI()
add_context_panel(app, sessions)


@app.get("/")
def index():
    # no-cache: browsers otherwise keep serving an old page after a redeploy
    return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-cache"})


CAPSULE_URL = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg"


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value] if value else []


async def game_cards(research: Research) -> list[dict]:
    """The games for the page's dossier header: appid and name, plus store facts when we have them.

    Uses get_game_info's evidence if a researcher called it; otherwise, with real tools, one
    store-details request (cached, 8 s budget). Missing facts stay missing: the page shows what it has."""
    cards = []
    items = research.store.items()
    for game in research.games:
        appid = game.get("appid")
        card = {**game, "store_url": f"https://store.steampowered.com/app/{appid}/",
                "header_image": CAPSULE_URL.format(appid=appid)}
        facts = None
        for item in items.values():
            if item["source"] == "store" and item["raw_id"] == str(appid):
                try:
                    facts = json.loads(item["text"])
                except ValueError:
                    pass
        if facts is None and not USE_FAKE_TOOLS and isinstance(appid, int):
            try:
                snap = await asyncio.wait_for(steam_news.fetch_game_details(appid), 8)
                data = steam_news._details_data(snap["data"], appid)
                facts = {"developer": data.get("developers"), "release_date": steam_news._release(data)[0],
                         "genres": [g["description"] for g in data.get("genres", []) if "description" in g],
                         **steam_news._price(data)}
                card["header_image"] = data.get("header_image") or card["header_image"]
            except Exception:  # the header is decoration: never fail the answer over it
                facts = None
        if facts:
            card.update({"developer": ", ".join(_as_list(facts.get("developer"))) or None,
                         "release_date": facts.get("release_date"),
                         "genres": _as_list(facts.get("genres"))[:4],
                         "price": facts.get("price"), "discount_pct": facts.get("discount_pct")})
        cards.append(card)
    return cards


def agent_timeline(hooks: TraceHooks) -> list[dict]:
    """When each agent started and finished, in seconds from the first one: shows the parallel fan-out."""
    if not hooks.timings:
        return []
    t0 = min(start for start, _ in hooks.timings.values())
    rows = [{"agent": agent, "start": round(start - t0, 1), "end": round(end - t0, 1) if end else None}
            for agent, (start, end) in hooks.timings.items()]
    return sorted(rows, key=lambda r: r["start"])


def build_chart(research: Research) -> dict | None:
    """Timeline data for the page: the review trend (from get_review_trend), plus the patch notes and
    monthly player counts the researchers found, taken from the evidence so each point has its ref."""
    chart = dict(research.chart or {})
    items = research.store.items()
    if not chart.get("patches"):
        chart["patches"] = [
            {"date": it["date"][:10], "title": it["text"].split("\n")[0][:90], "ref": rid}
            for rid, it in items.items() if it["source"] == "news"
        ]
    chart["players"] = [
        {"month": it["date"][:7], "avg": it["meta"]["avg"], "ref": rid}
        for rid, it in items.items() if it["source"] == "player_history" and "avg" in it.get("meta", {})
    ]
    return chart if chart.get("series") or chart["players"] else None


def research_error(exc: Exception) -> str:
    message = str(exc)
    if "default credentials were not found" in message.lower():
        return "Google Cloud login is missing on this computer. Run gcloud auth application-default login, then retry."
    if "could not resolve project_id" in message.lower():
        return "Set GOOGLE_CLOUD_PROJECT or provide a local gcp-project.txt before running Vertex AI."
    return f"{type(exc).__name__}: {message[:500]}"


def save_session(session_id: str, question: str, report: str, research: Research) -> None:
    """Remember this turn for follow-up questions, whichever endpoint ran it."""
    if research.games:
        last_games[session_id] = research.games
    history.setdefault(session_id, []).extend(
        [{"role": "user", "content": question}, {"role": "assistant", "content": report}]
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    session_id = request.session_id or str(uuid.uuid4())
    turns = history.setdefault(session_id, [])
    sessions[session_id] = AgentContexts()
    hooks = TraceHooks(sessions[session_id])
    research = Research(hooks=hooks)

    try:
        report = await deep_research(request.message, list(turns), research, last_games.get(session_id))
    except Exception as e:
        # Auth, quota, a model that won't stop: say what happened instead of a bare 500.
        raise HTTPException(status_code=502, detail=research_error(e))

    save_session(session_id, request.message, report, research)
    return ChatResponse(
        response=report,
        session_id=session_id,
        tool_calls=[ToolCall(**call) for call in hooks.tool_calls],
        evidence=research.store.items(),
        chart=build_chart(research),
        games=await game_cards(research),
        timeline=agent_timeline(hooks),
    )


@app.post("/clear")
def clear(session_id: str | None = None):
    """Forget one conversation (the page's delete button). Other sessions are untouched."""
    for store in (history, last_games, sessions):
        store.pop(session_id, None)
    return {"status": "ok"}


@app.get("/run")
async def run(question: str, session_id: str | None = None):
    """Research one question and stream what happens as Server-Sent Events (reference pattern).

    The "done" event carries the same fields as /chat's response (tool_calls, evidence, chart),
    and the turn is saved so a follow-up question in the same session works.
    """
    run_id = session_id or str(uuid.uuid4())
    sessions[run_id] = AgentContexts()
    queue: asyncio.Queue = asyncio.Queue()
    hooks = TraceHooks(sessions[run_id], queue.put_nowait)
    research = Research(hooks=hooks)

    async def work():
        try:
            report = await deep_research(question, list(history.get(run_id, [])), research, last_games.get(run_id))
            save_session(run_id, question, report, research)
            queue.put_nowait({
                "type": "done", "markdown": report,
                "tool_calls": hooks.tool_calls,
                "evidence": research.store.items(),
                "chart": build_chart(research),
            })
        except Exception as e:
            queue.put_nowait({"type": "error", "message": research_error(e)})

    task = asyncio.create_task(work())

    async def events():
        try:
            yield f"data: {json.dumps({'type': 'start', 'run_id': run_id})}\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event["type"] in ("done", "error"):
                    break
        finally:
            task.cancel()  # the page closed the stream: stop spending tokens

    return StreamingResponse(events(), media_type="text/event-stream")


if __name__ == "__main__":
    # Cloud Run sets PORT. Raise its request timeout to 300 s+ for deep research.
    uvicorn.run(app, host=args.host, port=args.port)
