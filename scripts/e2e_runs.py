"""Run the README's sample queries end to end against the real model and tools, plus one vague
question and one follow-up as a demo, saving each /chat response with timings to docs/sample_runs/.

Usage: uv run scripts/e2e_runs.py [name ...]      (names below; default: all)
"""

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
import tools  # noqa: E402
from detective.hooks import TraceHooks  # noqa: E402

OUT = ROOT / "docs" / "sample_runs"
PREFIX = "" if tools.USE_FAKE_TOOLS else "real_"  # fake runs keep the old names

# name, question, continue the previous run's session? The first three are the README's sample
# queries over the snapshot window (2026-08-01 to 2026-10-31); the rest demo clarify + follow-up.
RUNS = [
    ("q1_spike", "Which update caused Helldivers 2's negative reviews to spike between August and "
                 "October 2026, and what exactly are players complaining about?", False),
    ("q2_languages", "Do Chinese and English players complain about the same things in Baldur's Gate 3 "
                     "between August and October 2026?", False),
    ("q3_veterans", "How do players with 100+ hours review PUBG between August and October 2026 "
                    "differently from newcomers?", False),
    ("d_vague", "Is it good?", False),
    ("d_followup", "What about Chinese players compared to English players?", "q2_languages"),
]

# Keep each run's hooks so we can read agent timings (not part of the /chat response).
created: list[TraceHooks] = []
_init = TraceHooks.__init__


def _record(self, *args, **kwargs):
    _init(self, *args, **kwargs)
    created.append(self)


TraceHooks.__init__ = _record


def main(wanted: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = TestClient(app.app)
    sessions: dict[str, str] = {}
    for name, question, follow in RUNS:
        if wanted and name not in wanted:
            continue
        body = {"message": question, "session_id": sessions.get(follow) if follow else None}
        t0 = time.monotonic()
        r = client.post("/chat", json=body)
        elapsed = round(time.monotonic() - t0, 1)
        data = r.json()
        if r.status_code != 200:
            print(name, r.status_code, data)
            (OUT / f"{PREFIX}{name}.json").write_text(json.dumps({"question": question, "status": r.status_code, "error": data}, ensure_ascii=False, indent=2))
            continue
        if follow:
            sessions[name] = sessions[follow]
        else:
            sessions[name] = data["session_id"]
        hooks = created[-1]
        start = min((t[0] for t in hooks.timings.values()), default=t0)
        timings = {agent: [round(a - start, 1), round(b - start, 1) if b else None] for agent, (a, b) in hooks.timings.items()}
        cited = sorted(set(re.findall(r"\[(R\d+)\]", data["response"])), key=lambda x: int(x[1:]))
        record = {
            "question": question,
            "seconds": elapsed,
            "tool_calls_per_agent": dict(Counter(c["agent"] for c in data["tool_calls"])),
            "agent_timings_s": timings,
            "cited": cited,
            "cited_but_missing": [c for c in cited if c not in data["evidence"]],
            **data,
        }
        (OUT / f"{PREFIX}{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        print(f"== {name}: {elapsed}s, {record['tool_calls_per_agent']}, cited {len(cited)}, missing {record['cited_but_missing']}")
        print(json.dumps(timings))
        print(data["response"][:1500], "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
