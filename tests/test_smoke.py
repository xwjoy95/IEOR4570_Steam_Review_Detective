"""Offline tests: no model calls, no network. Runner.run is replaced where an agent would run."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from agents.tool_context import ToolContext
from fastapi.testclient import TestClient

import app
import tools
from detective import researchers, writer
from detective.hooks import TraceHooks
from detective.researchers import ResearcherReport, research
from detective.state import MAX_RESEARCHERS, Research
from evidence import EvidenceStore
from tools import fakes, player_stats, steam_news, steam_reviews

REAL = [steam_news.search_game, steam_news.get_game_info, steam_news.get_patch_notes, steam_reviews.get_review_trend,
        steam_reviews.sample_reviews, steam_reviews.review_stats, player_stats.get_player_stats, player_stats.get_player_history]


def run(coro):
    return asyncio.run(coro)


def tool_ctx(state, name, args):
    return ToolContext(context=state, tool_name=name, tool_call_id="call_1", tool_arguments=json.dumps(args))


# --- Evidence store ---


def test_evidence_store():
    store = EvidenceStore()
    r1 = store.add("review", "123", "https://example.com/1", "2026-09-09", "Lost my save")
    r2 = store.add("news", "abc", "https://example.com/2", "2026-09-09", "Patch 1.4")
    assert (r1, r2) == ("R1", "R2")
    assert store.add("review", "123", "", "", "") == "R1"  # same item keeps its id
    assert store.get("R2").text == "Patch 1.4"
    assert store.validate("Saves broke [R1][R2], see [R9].") == ["R9"]


# --- Fake tools ---


def test_fakes_keep_the_real_schemas():
    assert tools.USE_FAKE_TOOLS
    assert len(tools.__all__) == 8
    for real in REAL:
        fake = getattr(tools, real.name)
        assert fake is not real
        assert fake.params_json_schema == real.params_json_schema
        assert fake.description == real.description


def test_fakes_return_refs_from_the_store():
    state = Research(hooks=TraceHooks())
    out = json.loads(run(tools.sample_reviews.on_invoke_tool(
        tool_ctx(state, "sample_reviews", {}),
        json.dumps({"appid": fakes.APPID, "start": "2026-09-09", "end": "2026-09-20", "review_type": "negative",
                    "language": "schinese", "min_hours": None, "max_hours": None, "limit": 5}),
    )))
    assert out["ok"] and out["reviews"]
    assert all(r["language"] == "schinese" and not r["positive"] for r in out["reviews"])
    assert all(state.store.get(r["ref"]) for r in out["reviews"])

    trend = json.loads(run(tools.get_review_trend.on_invoke_tool(tool_ctx(state, "get_review_trend", {}),
                                                                  json.dumps({"appid": fakes.APPID, "language": "all"}))))
    assert trend["spikes"][0]["period"] == "2026-09-07"
    assert state.chart and state.chart["series"]


# --- research(): numbered clones, the cap, evidence filtering ---


def test_research_numbers_clones_caps_and_drops_fake_ids(monkeypatch):
    state = Research(hooks=TraceHooks())
    real_id = state.store.add("review", "1", "u", "2026-09-10", "Lost my save")
    names = []

    async def fake_run(agent, prompt, **kwargs):
        names.append(agent.name)
        report = ResearcherReport(findings=[
            {"claim": "Saves were lost", "evidence": [real_id, "R99"]},
            {"claim": "Made up", "evidence": ["R42"]},
        ])
        return SimpleNamespace(final_output_as=lambda cls: report)

    monkeypatch.setattr(researchers.Runner, "run", fake_run)
    state.lead_turns = 1
    args = {"researcher": "reviews", "task": "What do reviewers complain about?"}
    out = json.loads(run(research.on_invoke_tool(tool_ctx(state, "research", args), json.dumps(args))))

    assert names == ["Review Analyst 1"]
    assert out["findings"] == [{"claim": "Saves were lost", "evidence": [real_id]}]  # R99 and R42 dropped
    assert any("Made up" in c for c in out["cannot_answer"])
    assert state.findings[0]["round"] == 1

    for _ in range(MAX_RESEARCHERS):  # same Lead turn: same round, until the cap
        run(research.on_invoke_tool(tool_ctx(state, "research", args), json.dumps(args)))
    assert state.researchers == MAX_RESEARCHERS
    assert "No researchers left" in run(research.on_invoke_tool(tool_ctx(state, "research", args), json.dumps(args)))


def test_research_caps_rounds(monkeypatch):
    state = Research(hooks=TraceHooks())

    async def fake_run(agent, prompt, **kwargs):
        return SimpleNamespace(final_output_as=lambda cls: ResearcherReport(findings=[]))

    monkeypatch.setattr(researchers.Runner, "run", fake_run)
    args = {"researcher": "players", "task": "t"}
    for turn in (1, 2, 3):
        state.lead_turns = turn
        run(research.on_invoke_tool(tool_ctx(state, "research", args), json.dumps(args)))
    state.lead_turns = 4
    assert "No rounds left" in run(research.on_invoke_tool(tool_ctx(state, "research", args), json.dumps(args)))


# --- write(): citation check and one rewrite ---


def test_write_rewrites_once_then_strips(monkeypatch):
    state = Research(hooks=TraceHooks())
    rid = state.store.add("review", "1", "u", "2026-09-10", "Lost my save")
    state.findings = [{"researcher": "Review Analyst 1", "round": 1, "task": "t",
                       "findings": [{"claim": "Saves lost", "evidence": [rid]}], "cannot_answer": []}]
    drafts = iter(["Saves were lost [R1][R7].", "Saves were lost [R1][R8]."])
    prompts = []

    async def fake_run(agent, prompt, **kwargs):
        prompts.append(prompt)
        return SimpleNamespace(final_output=next(drafts))

    monkeypatch.setattr(writer.Runner, "run", fake_run)
    report = run(writer.write("Why?", state))
    assert len(prompts) == 2 and "R7" in prompts[1]  # rewrite was told what was wrong
    assert report == "Saves were lost [R1]."  # still-bad id stripped
    assert "Lost my save" in prompts[0]  # the Writer sees the evidence it may quote


# --- Hooks ---


def test_hooks_record_agent_and_args():
    hooks = TraceHooks()
    state = Research(hooks=hooks)
    agent = SimpleNamespace(name="Review Analyst 2")
    tool = SimpleNamespace(name="sample_reviews")
    run(hooks.on_tool_end(tool_ctx(state, "sample_reviews", {"appid": 1, "start": "2026-09-01"}), agent, tool, "x" * 5000))
    call = hooks.tool_calls[0]
    assert call["agent"] == "Review Analyst 2" and call["args"] == {"appid": 1, "start": "2026-09-01"}
    assert len(call["result"]) == 2000


# --- App ---


def test_index_and_chat(monkeypatch):
    async def fake_deep_research(question, history, research, last_games=None):
        research.games = [{"appid": 1, "name": "G"}]
        research.store.add("review", "1", "u", "2026-09-10", "t")
        research.hooks.tool_calls.append({"agent": "Lead", "name": "get_review_trend", "args": {}, "result": "{}"})
        return f"answer to {question} after {len(history)} turns"

    monkeypatch.setattr(app, "deep_research", fake_deep_research)
    client = TestClient(app.app)
    assert client.get("/").status_code == 200
    first = client.post("/chat", json={"message": "q1"}).json()
    assert first["tool_calls"][0]["agent"] == "Lead" and "R1" in first["evidence"]
    second = client.post("/chat", json={"message": "q2", "session_id": first["session_id"]}).json()
    assert second["response"] == "answer to q2 after 2 turns"
    assert app.last_games[first["session_id"]] == [{"appid": 1, "name": "G"}]
