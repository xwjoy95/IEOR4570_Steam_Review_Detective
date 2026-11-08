"""The Writer: turns the findings into a cited report, and the citation check that sends it back once."""

import json
import re

from agents import Agent, Runner

from detective.llm import SETTINGS, SMART
from detective.state import Research
from evidence import CITATION

WRITER_PROMPT = """\
Write a short report in markdown that answers the question, using only the findings you're given.
The reader is a player deciding whether to buy or return to a game, or a developer studying it.

Keep it under about 700 words. Cover the time window the question asks about; older events get
at most one or two lines of background.

Structure, in this order:
1. A heading that restates the question, then a one-paragraph answer. Open with the overall
   picture in numbers (e.g. "about 96% of reviews in this window are positive"), then what changed,
   when, and why. Only frame it as a decline or a problem if the data shows one or the user asked
   about one.
2. **Timeline**: dated bullets, what happened when (updates, review spikes, player-count changes).
3. **What players say**: start with the positive/negative split, then two parts, praise and
   criticism, sized by that split: when most reviews are positive, praise comes first and gets
   more room; when most are negative, criticism does. Quote 2 to 3 reviews from the evidence list
   in their original language (with hours played if known), from both sides when both exist, in
   roughly the same proportion. Compare groups (languages, veterans vs newcomers) when the
   findings do.
4. **What the data can't answer**: everything in cannot_answer that matters to the question, plus
   the standing limits (Steam reviews only reflect players who write reviews; SteamSpy owner counts
   are estimates; no console data; developers' motives only as stated in announcements).

Citations:
- End every sentence that states a fact with the evidence ids from its finding, like [R3] or [R3][R7].
  Cite at most 3 ids per sentence: pick the most direct ones.
- Use only ids that appear in the findings. Never invent one, never renumber them.
- Don't add facts, numbers or dates that aren't in the findings or the evidence list.

Style:
- Write everything, headings included, in the language the user asked in (translate the section
  names above). Keep quoted reviews in their original language.
- If two numbers contradict each other (say, an owner estimate lower than the players online
  right now), don't report either as fact: say the source looks out of date.
- Use **bold** only; don't use single asterisks or underscores for italics, game titles included.
- Copy numbers exactly as the findings and evidence give them: don't round up, generalise or
  exaggerate them ("200 hours" never becomes "thousands of hours").
- Write plain, specific sentences with dates and numbers. Don't claim more significance than the
  findings do: no "profoundly", "pivotal", "paving the way", "it is worth noting", "a testament to"."""

writer = Agent(
    name="Writer",
    instructions=WRITER_PROMPT,
    model=SMART,
    model_settings=SETTINGS,
)

MAX_QUOTE_CHARS = 300


def writer_input(question: str, research: Research) -> str:
    """The question, the findings, and the evidence they cite (so the Writer can quote reviews)."""
    cited = sorted({rid for f in research.findings for item in f["findings"] for rid in item["evidence"]}, key=lambda r: int(r[1:]))
    evidence = []
    for rid in cited:
        item = research.store.get(rid)
        meta = ", ".join(f"{k}={v}" for k, v in item.meta.items())
        evidence.append(f"[{rid}] {item.source}, {item.date}{', ' + meta if meta else ''}: {item.text[:MAX_QUOTE_CHARS]}")
    findings = [{k: f[k] for k in ("researcher", "task", "findings", "cannot_answer")} for f in research.findings]
    return (
        f"Question: {question}\n\n"
        f"Findings:\n{json.dumps(findings, ensure_ascii=False, indent=1)}\n\n"
        "Evidence list (what the ids point to):\n" + "\n".join(evidence)
    )


def strip_citations(report: str, missing: list[str]) -> str:
    """Remove citations that don't exist, as a last resort after the rewrite."""
    bad = set(missing)
    return CITATION.sub(lambda m: "" if m.group(1) in bad else m.group(0), report)


async def write(question: str, research: Research) -> str:
    """Write the report, check every [R#] exists, and rewrite once if any doesn't."""
    prompt = writer_input(question, research)
    result = await Runner.run(writer, prompt, context=research, hooks=research.hooks)
    report = str(result.final_output)

    missing = research.store.validate(report)
    if missing:
        fix = (
            f"{prompt}\n\nYour previous draft cited ids that don't exist: {', '.join(missing)}. "
            f"Rewrite it citing only ids from the findings.\n\nPrevious draft:\n{report}"
        )
        result = await Runner.run(writer, fix, context=research, hooks=research.hooks)
        report = str(result.final_output)
        report = strip_citations(report, research.store.validate(report))
    return re.sub(r"[ \t]+\n", "\n", report).strip()
