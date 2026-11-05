"""FastAPI app: the page, POST /chat (Project 1's response shape), and GET /run (live events, as in
the course reference). Endpoints only: the agents live in detective/, the data tools in tools/."""

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

from context_panel import AgentContexts, add_context_panel  # noqa: E402
from detective.hooks import TraceHooks  # noqa: E402
from detective.orchestrator import deep_research  # noqa: E402
from detective.state import Research  # noqa: E402


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
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {str(e)[:500]}")

    if research.games:
        last_games[session_id] = research.games
    turns += [{"role": "user", "content": request.message}, {"role": "assistant", "content": report}]
    return ChatResponse(
        response=report,
        session_id=session_id,
        tool_calls=[ToolCall(**call) for call in hooks.tool_calls],
        evidence=research.store.items(),
        chart=research.chart,
    )


@app.get("/run")
async def run(question: str, session_id: str | None = None):
    """Research one question and stream what happens as Server-Sent Events (reference pattern).

    TODO(Jiashu): emit activity/context events from TraceHooks as the agents work, and send
    tool_calls, evidence and chart with "done".
    """
    run_id = session_id or str(uuid.uuid4())
    sessions[run_id] = AgentContexts()
    queue: asyncio.Queue = asyncio.Queue()
    hooks = TraceHooks(sessions[run_id], queue.put_nowait)
    research = Research(hooks=hooks)

    async def work():
        try:
            report = await deep_research(question, list(history.get(run_id, [])), research, last_games.get(run_id))
            queue.put_nowait({"type": "done", "markdown": report, "evidence": research.store.items(), "chart": research.chart})
        except Exception as e:
            queue.put_nowait({"type": "error", "message": f"{type(e).__name__}: {str(e)[:500]}"})

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
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
