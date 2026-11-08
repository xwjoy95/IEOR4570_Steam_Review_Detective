"""The README's baseline: the same model as the agents, with no tools and no system prompt.
One plain completion per question, saved to docs/baselines/<name>.md so the README can put it
next to the agent's answer. Keep the questions identical to the README's sample queries.

Usage: uv run scripts/baseline.py [name ...]
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import litellm  # noqa: E402

from detective.llm import MODEL, resolve_vertex_project  # noqa: E402

OUT = ROOT / "docs" / "baselines"

# name, question: the README's three sample queries, verbatim.
QUESTIONS = [
    ("q1_spike", "Which update caused Helldivers 2's negative reviews to spike between August and "
                 "October 2026, and what exactly are players complaining about?"),
    ("q2_languages", "Do Chinese and English players complain about the same things in Baldur's Gate 3 "
                     "between August and October 2026?"),
    ("q3_veterans", "How do players with 100+ hours review PUBG between August and October 2026 "
                    "differently from newcomers?"),
]


async def baseline_answer(question: str) -> str:
    """One model call: no tools, no system prompt, the same model and settings as the agents."""
    response = await litellm.acompletion(
        model=MODEL,
        messages=[{"role": "user", "content": question}],
        vertex_project=resolve_vertex_project(),
        vertex_location=os.environ.get("VERTEX_LOCATION") or os.environ.get("VERTEXAI_LOCATION", "global"),
        timeout=90,
        num_retries=2,
    )
    return response.choices[0].message.content or ""


async def main(wanted: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, question in QUESTIONS:
        if wanted and name not in wanted:
            continue
        answer = await baseline_answer(question)
        path = OUT / f"{name}.md"
        path.write_text(f"<!-- Baseline: the same model, no tools, no system prompt -->\n\n"
                        f"**Question:** {question}\n\n**Answer:**\n\n{answer}\n", encoding="utf-8")
        print(f"== {name} -> {path.relative_to(ROOT)}\n{answer[:600]}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="No-tool model baselines for the README's sample queries.")
    parser.add_argument("names", nargs="*", help="only these question names, e.g. q1_spike")
    asyncio.run(main(parser.parse_args().names))
