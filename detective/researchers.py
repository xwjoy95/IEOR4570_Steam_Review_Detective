"""The three researchers: their prompts, their tool subsets, and the `research` tool the Lead uses to
send one out. Each researcher runs in a fresh context and sees only its task and its own tools."""

import json
from typing import Literal

from agents import Agent, MaxTurnsExceeded, RunContextWrapper, Runner, function_tool
from pydantic import BaseModel

from detective.llm import CHEAP, SETTINGS
from detective.state import MAX_RESEARCHERS, MAX_ROUNDS, Research
from tools import (
    get_game_info,
    get_patch_notes,
    get_player_history,
    get_player_stats,
    get_review_trend,
    review_stats,
    sample_reviews,
)


class Finding(BaseModel):
    claim: str
    evidence: list[str]  # ["R3", "R7"]


class ResearcherReport(BaseModel):
    findings: list[Finding]
    cannot_answer: list[str] = []


# --- Prompts ---

_RULES = """\
Rules:
- Use only your tools, and answer only your task. Make at most 6 tool calls.
- Every finding needs evidence: the "ref" ids (like "R12") from your tool results that support it.
  Never invent an id. A claim with no ref from a tool result doesn't go in findings.
- Take numbers only from tool results. Don't estimate, extrapolate or do your own arithmetic beyond
  simple differences; say "sampled" when a tool says its reviews are a sample.
- At most 8 findings. Each is one specific, checkable sentence: dates, counts, percentages, who said what.
- Put the parts of your task the tools can't answer in cannot_answer. Don't guess.
- If a tool returns ok: false, try once more with different arguments, then report the gap.
- Dates are YYYY-MM-DD."""

# TODO(A): owner please review
REVIEWS_PROMPT = f"""\
You are the Review Analyst on a team investigating why a Steam game's player sentiment changed.
You read Steam user reviews: the weekly trend, samples of reviews from a time window, and breakdowns
by language or playtime.

How to work:
1. If your task names no time window, call get_review_trend first and use its spikes to pick one.
2. Use sample_reviews to read what players actually say in that window (negative reviews unless the
   task asks otherwise). Filter by language ("english", "schinese", "tchinese") or playtime
   (min_hours / max_hours) when the task compares groups.
3. Use review_stats for group comparisons (group_by "language" or "playtime").
4. Summarise the complaints in your own words and cite the reviews that show them. When a quote
   matters, keep it in its original language.

{_RULES}"""

# TODO(B): owner please review
PATCHES_PROMPT = f"""\
You are the Patch Historian on a team investigating why a Steam game's player sentiment changed.
You read the developer's announcements and patch notes, and the game's store details.

How to work:
1. Call get_patch_notes for the window in your task (widen it by a couple of weeks on each side so
   you don't miss the update that started things). Use official_only=false only when the task
   asks for press coverage.
2. Report what each relevant update changed, in the developer's own words where it matters, with
   its date. Report the developer's stated reasons only if an announcement states them; never
   speculate about motives.
3. Call get_game_info when the task needs price, release date, developer or genre. It has only the
   current price, no price history.

{_RULES}"""

# TODO(C): owner please review
PLAYERS_PROMPT = f"""\
You are the Player Data Analyst on a team investigating why a Steam game's player sentiment changed.
You check whether players actually left: monthly player counts, current players, and SteamSpy's
estimates.

How to work:
1. Call get_player_history for the months in your task (include a couple of months before, as a
   baseline), and report the change in average players month by month.
2. Call get_player_stats for current players, owner estimates, playtime and tags. Always call
   owners_estimate a SteamSpy estimate, never a sales figure. A missing playtime means "no data".
3. Say plainly whether the player numbers moved with the change the task describes, or not.

{_RULES}"""


def _researcher(name: str, instructions: str, tools: list) -> Agent:
    return Agent(
        name=name,
        instructions=instructions,
        model=CHEAP,
        model_settings=SETTINGS,
        tools=tools,
        output_type=ResearcherReport,
    )


RESEARCHERS: dict[str, Agent] = {
    "reviews": _researcher("Review Analyst", REVIEWS_PROMPT, [get_review_trend, sample_reviews, review_stats]),  # A
    "patches": _researcher("Patch Historian", PATCHES_PROMPT, [get_patch_notes, get_game_info]),  # B
    "players": _researcher("Player Data Analyst", PLAYERS_PROMPT, [get_player_stats, get_player_history]),  # C
}


def keep_real_evidence(report: ResearcherReport, research: Research) -> ResearcherReport:
    """Drop evidence ids that aren't in the store; a claim left with none moves to cannot_answer."""
    findings, unsupported = [], []
    for finding in report.findings:
        ids = [rid for rid in finding.evidence if research.store.get(rid)]
        if ids:
            findings.append(Finding(claim=finding.claim, evidence=ids))
        else:
            unsupported.append(f"Unsupported (no valid evidence): {finding.claim}")
    return ResearcherReport(findings=findings, cannot_answer=report.cannot_answer + unsupported)


def _game_line(research: Research) -> str:
    return "; ".join(f"{g['name']} (appid {g['appid']})" for g in research.games) or "unknown"


@function_tool
async def research(ctx: RunContextWrapper[Research], researcher: Literal["reviews", "patches", "players"], task: str) -> str:
    """Send one researcher, in a fresh context, to answer one task. Returns its findings, each with the
    evidence ids that support it.

    Args:
        researcher: "reviews" (Steam reviews: trend, samples, language/playtime splits), "patches"
            (announcements, patch notes, store details) or "players" (player counts, SteamSpy estimates).
        task: One specific thing to find out: the game, the time window, and the exact question.
    """
    state = ctx.context
    # A new round is a Lead turn that hasn't sent researchers yet. Researchers sent in the same turn share a round.
    if state.lead_turns not in state.round_turns:
        if len(state.round_turns) >= MAX_ROUNDS:
            return "No rounds left. Reply DONE."
        state.round_turns.append(state.lead_turns)
    if state.researchers >= MAX_RESEARCHERS:
        return "No researchers left. Reply DONE."

    # What researcher.as_tool() would do, written out so each run gets a numbered name: its own
    # column in the context panel, and its own label in tool_calls.
    state.researchers += 1
    agent = RESEARCHERS[researcher].clone(name=f"{RESEARCHERS[researcher].name} {state.researchers}")
    prompt = f"Game: {_game_line(state)}\nTask: {task}"
    try:
        result = await Runner.run(agent, prompt, context=state, hooks=state.hooks, max_turns=10)
        report = keep_real_evidence(result.final_output_as(ResearcherReport), state)
    except MaxTurnsExceeded:
        report = ResearcherReport(findings=[], cannot_answer=[f"{agent.name} ran out of turns before finishing: {task}"])

    state.findings.append(
        {"researcher": agent.name, "round": state.round_turns.index(state.lead_turns) + 1, "task": task, **report.model_dump()}
    )
    return json.dumps(report.model_dump(), ensure_ascii=False)
