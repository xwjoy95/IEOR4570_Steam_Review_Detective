"""The whole run: clarify -> plan and research in parallel (with gap rounds) -> write -> check citations."""

from agents import MaxTurnsExceeded, Runner

from detective.lead import Clarification, clarifier, lead
from detective.state import Research
from detective.writer import write

HISTORY_TURNS = 6  # earlier messages the Clarifier sees
HISTORY_CHARS = 1_500  # per message: an earlier report is long, its gist is enough


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
    lines.append(f"Question: {question}")
    return "\n".join(lines)


async def deep_research(question: str, history: list[dict], research: Research, last_games: list[dict] | None = None) -> str:
    """Answer one question. Returns the report's markdown, or a question back to the user.

    history: earlier turns of this session ({role, content}), so follow-ups ("那中文玩家呢？") work.
    research: this run's state; app.py reads tool calls, evidence and research.games from it.
    last_games: the games the previous question in this session was about.
    """
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
    prompt = f"Question: {question}\nGame: {games}\nFocus: {clarification.focus or question}"
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
