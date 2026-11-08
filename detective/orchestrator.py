"""The whole run: clarify -> plan and research in parallel (with gap rounds) -> write -> check citations.

The run has a wall-clock budget (RESEARCH_TIMEOUT, under Cloud Run's request timeout): if it runs
out, whatever the researchers found is reported as it stands instead of failing."""

import asyncio
import os
from datetime import date

from agents import MaxTurnsExceeded, Runner

from detective.lead import Clarification, clarifier, lead
from detective.state import Research
from detective.writer import write

HISTORY_TURNS = 6  # earlier messages the Clarifier sees
HISTORY_CHARS = 1_500  # per message: an earlier report is long, its gist is enough
RESEARCH_TIMEOUT = int(os.environ.get("RESEARCH_TIMEOUT", "240"))  # seconds; Cloud Run gets 300s+


def clarifier_input(question: str, history: list[dict], last_games: list[dict]) -> str:
    lines = []
    if history:
        lines.append("Conversation so far:")
        for turn in history[-HISTORY_TURNS:]:
            lines.append(f"{turn['role']}: {turn['content'][:HISTORY_CHARS]}")
        lines.append("")
    if last_games:
        lines.append("Game(s) from the previous question: " + "; ".join(f"{g['name']} (appid {g['appid']})" for g in last_games))
        lines.append("")
    lines.append(f"Today: {date.today().isoformat()}")
    lines.append(f"Question: {question}")
    return "\n".join(lines)


async def deep_research(question: str, history: list[dict], research: Research, last_games: list[dict] | None = None) -> str:
    """Answer one question within the time budget. Returns the report's markdown, or a question back.

    history: earlier turns of this session ({role, content}), so follow-ups ("what about Chinese
    players?") work. research: this run's state; app.py reads tool calls, evidence and
    research.games from it. last_games: the games the previous question in this session was about.

    If the budget runs out mid-run, the findings gathered so far are reported as they stand.
    """
    try:
        return await asyncio.wait_for(_answer(question, history, research, last_games), RESEARCH_TIMEOUT)
    except (asyncio.TimeoutError, TimeoutError):
        return fallback_report(research)


def fallback_report(research: Research) -> str:
    """A degraded report without the Writer: the findings as they stand, citations included
    (research() already dropped ids that aren't in the store)."""
    if not research.findings:
        return ("The research ran out of time before finding anything. Try a narrower question: "
                "one game, and a specific month or update.")
    lines = [
        "**The research hit its time budget before the Writer could write the full report.**",
        "What the researchers found so far:",
    ]
    for finding in research.findings:
        for item in finding["findings"]:
            cites = "".join(f" [{rid}]" for rid in item["evidence"])
            lines.append(f"- {item['claim']}{cites} *({finding['researcher']})*")
    gaps = [gap for finding in research.findings for gap in finding.get("cannot_answer", [])]
    if gaps:
        lines += ["", "What the data can't answer:"] + [f"- {gap}" for gap in gaps]
    return "\n".join(lines)


async def _answer(question: str, history: list[dict], research: Research, last_games: list[dict] | None) -> str:
    # 1. Clarify: stop here and ask back if the question is too vague.
    result = await Runner.run(
        clarifier, clarifier_input(question, history, last_games or []), context=research, hooks=research.hooks, max_turns=6
    )
    clarification = result.final_output_as(Clarification)
    if clarification.vague or not clarification.games:
        return clarification.ask or "Which Steam game do you mean? Please give its exact name."
    research.games = [g.model_dump() for g in clarification.games]

    # 2. Research: the Lead plans, fans out researchers in parallel, and goes back for gaps.
    games = "; ".join(f"{g['name']} (appid {g['appid']})" for g in research.games)
    prompt = f"Today: {date.today().isoformat()}\nQuestion: {question}\nGame: {games}\nFocus: {clarification.focus or question}"
    if clarification.window:
        prompt += f"\nWindow: {' to '.join(clarification.window)}"
    try:
        await Runner.run(lead, prompt, context=research, hooks=research.hooks, max_turns=12)
    except MaxTurnsExceeded:
        pass  # write up whatever the researchers found

    if not research.findings:
        return "The researchers didn't find anything to report for this question. Try naming a time range or a specific update."

    # 3 + 4. Write, check that every [R#] exists, rewrite once if not.
    return await write(question, research)
