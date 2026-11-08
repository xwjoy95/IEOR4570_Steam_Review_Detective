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
- The report is checked automatically after you write it: every number and every quoted passage
  must appear in the evidence its sentence cites. Copy numbers and quotes exactly.

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

NUMBER = re.compile(r"\d+(?:\.\d+)?")  # ids like [R12] are removed before this runs
QUOTE = re.compile(r'"([^"]{10,})"|\u201c([^\u201d]{10,})\u201d')  # "..." or \u201c...\u201d


def _norm(text: str) -> str:
    """Letters, digits and CJK only, lowercased: quotes may differ in punctuation and spacing."""
    return re.sub(r"[\W_]+", "", text.lower())


def _has_number(corpus: str, num: str) -> bool:
    """The number as-is (2026-09-09 contains 9; not 4 in 41), commas ignored on both sides."""
    return re.search(rf"(?<!\d){re.escape(num)}(?!\d)", corpus.replace(",", "")) is not None


def grounding_issues(report: str, store) -> list[str]:
    """Deterministic grounding checks beyond citation existence: in every line that carries
    citations, each number and each quoted passage must appear in the evidence that line cites.
    Returns one human-readable issue per problem; an empty list means the report is grounded."""
    issues = []
    for line in report.splitlines():
        ids = sorted(set(CITATION.findall(line)), key=lambda r: int(r[1:]))
        if not ids:
            continue
        plain = " ".join(
            item.text + " " + str(item.meta) for rid in ids if (item := store.get(rid))
        )
        for num in NUMBER.findall(CITATION.sub("", line)):
            if not _has_number(plain, num):
                issues.append(f"the number {num} in \u201c{line.strip()[:60]}\u201d does not appear in the cited evidence ({', '.join(ids)})")
        joined = _norm(plain)
        for match in QUOTE.findall(line):
            quote = (match[0] or match[1]).strip()
            if _norm(quote) not in joined:
                issues.append(f"the quoted text \u201c{quote[:60]}\u201d is not in the cited evidence ({', '.join(ids)})")
    return issues


def caveat(issues: list[str]) -> str:
    """The degraded ending when grounding still fails after the rewrite: flag it, don't hide it."""
    return ("*Note (automated check): the following could not be verified against the cited sources "
            "and may be inaccurate:*\n" + "\n".join(f"- {issue}" for issue in issues))


def _fix_prompt(prompt: str, report: str, missing: list[str], issues: list[str]) -> str:
    problems = []
    if missing:
        problems.append(f"these cited ids don't exist: {', '.join(missing)}")
    if issues:
        problems.append("these statements are not grounded in the evidence they cite:\n" +
                        "\n".join(f"- {issue}" for issue in issues))
    return (
        f"{prompt}\n\nYour previous draft had problems: " + "\n".join(problems) +
        ".\nRewrite it citing only ids from the findings, copying every number and quote exactly "
        f"from the evidence.\n\nPrevious draft:\n{report}"
    )


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
    """Write the report, then check it twice: every [R#] must exist, and every number and quote
    must be grounded in the evidence it cites. One rewrite; anything still unsupported is either
    stripped (bad ids) or flagged in a caveat at the end (ungrounded statements)."""
    prompt = writer_input(question, research)
    result = await Runner.run(writer, prompt, context=research, hooks=research.hooks)
    report = str(result.final_output)

    missing = research.store.validate(report)
    issues = grounding_issues(report, research.store)
    if missing or issues:
        result = await Runner.run(writer, _fix_prompt(prompt, report, missing, issues),
                                  context=research, hooks=research.hooks)
        report = str(result.final_output)
        report = strip_citations(report, research.store.validate(report))
        remaining = grounding_issues(report, research.store)
        if remaining:
            report = f"{report}\n\n{caveat(remaining)}"
    return re.sub(r"[ \t]+\n", "\n", report).strip()
