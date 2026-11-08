"""The three researchers: their prompts, their tool subsets, and the `research` tool the Lead uses to
send one out. Each researcher runs in a fresh context and sees only its task and its own tools."""

import dataclasses
import json
from typing import Literal

from agents import Agent, MaxTurnsExceeded, RunContextWrapper, Runner, function_tool
from pydantic import BaseModel

from detective.llm import CHEAP, SETTINGS
from detective.state import MAX_RESEARCHERS, MAX_ROUNDS, MAX_TOOL_CALLS, Research
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
- When a tool result says from_snapshot: true, its data comes from the saved snapshot, not the live
  Steam API: mention in the finding that it is snapshot data and give the tool's as_of date.
- Dates are YYYY-MM-DD."""

REVIEWS_PROMPT = f"""\
You are the Review Analyst on a team investigating why a Steam game's player sentiment changed.
You read Steam user reviews: the weekly trend, samples of reviews from a time window, and breakdowns
by language or playtime.

How to work:
Tool parameter rules:
- Pass each tool argument as a separate structured parameter. Never combine a date with another parameter.
- Use YYYY-MM-DD for start and end dates, without any additional text.
- get_review_trend supports language="all" only. Do not request language-specific trends from this tool.
- For language comparisons, use review_stats with group_by="language".
- For sample_reviews, use language="english" or language="schinese" for English or Simplified Chinese reviews.
- When comparing language groups, clearly distinguish sampled statistics from overall population statistics.
- If a tool reports an invalid argument or unsupported language, correct the parameters before retrying. Do not repeatedly submit the same failed request.
1. If your task names no time window, call get_review_trend first and use its spikes to pick one.
2. Get the positive/negative split for the window first (get_review_trend's series, or
   review_stats) and report it as your first finding.
3. Use sample_reviews to read what players actually say: call it once with review_type="positive"
   and once with review_type="negative", so both sides are heard. Only sample one side when the
   task asks about just that side (e.g. "why did reviews drop"). Filter by language ("english",
   "schinese", "tchinese") or playtime (min_hours / max_hours) when the task compares groups.
4. Use review_stats for group comparisons (group_by "language" or "playtime").
5. Summarise what players praise and what they criticise, in your own words, giving each side room
   in proportion to the split. Cite the reviews that show each point. When a quote matters, keep it
   in its original language.

{_RULES}"""

# Steam announcements and store details.
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

# C: written against the real tools (tools/player_stats.py)
PLAYERS_PROMPT = f"""\
You are the Player Data Analyst on a team investigating why a Steam game's player sentiment changed.
You check whether players actually left: monthly player counts, current players, and SteamSpy's
estimates.

How to work:
1. Call get_player_history for the months in your task, starting two or three months earlier as a
   baseline. Dates are YYYY-MM-DD; only the month matters. Report average players month by month
   and each month's gain_pct exactly as returned. The numbers are average concurrent players
   (SteamCharts), not sales or unique players. If it returns no_data, check "coverage" or the
   message for the months it has, and retry once inside them.
2. Call get_player_stats for current players, yesterday's peak (ccu_yesterday), the owner estimate
   and tags. Always call owners_estimate a SteamSpy estimate range, never a sales figure. If
   owners_unreliable is true, don't report the owner range at all: say SteamSpy hasn't caught up. A null
   field means SteamSpy has no data: say "no data", never "0 hours" or "0 players".
3. Say plainly whether average players moved with the change the task describes, and when. Don't
   claim the reviews caused it: the data shows timing, not cause.
4. If a tool returns unavailable, put "historical player counts" (or "current player data") in
   cannot_answer.

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


def _capped(tool, counter: dict):
    """A copy of one tool that refuses calls once this researcher is out, instead of trusting the prompt."""
    async def invoke(ctx, input_json: str) -> str:
        counter["calls"] += 1
        if counter["calls"] > MAX_TOOL_CALLS:
            return json.dumps({
                "ok": False, "error": "tool_call_limit",
                "message": f"Tool call limit ({MAX_TOOL_CALLS}) reached. Report your findings now.",
            }, ensure_ascii=False)
        return await tool.on_invoke_tool(ctx, input_json)
    return dataclasses.replace(tool, on_invoke_tool=invoke)


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
    base = RESEARCHERS[researcher]
    counter = {"calls": 0}
    agent = base.clone(name=f"{base.name} {state.researchers}",
                       tools=[_capped(tool, counter) for tool in base.tools])
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
