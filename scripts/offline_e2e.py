"""Run the README's sample queries with every Steam host blocked, so the answers can only come from
data/snapshots/. This verifies the demo still works when Steam is down, rate-limits us, or blocks
Cloud Run's IPs, and that the report says the data came from snapshots.

Usage: uv run scripts/offline_e2e.py
"""

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
from detective.hooks import TraceHooks  # noqa: E402
from e2e_runs import RUNS  # noqa: E402  (same questions as the README's sample queries)

OUT = ROOT / "docs" / "sample_runs"
PREFIX = "offline_"

STEAM_HOSTS = {
    "store.steampowered.com", "api.steampowered.com", "steamspy.com", "steamcharts.com",
    "steamcommunity.com",
}

# Block every Steam host at the socket layer; Google (Vertex AI) stays reachable.
_real_send = httpx.AsyncClient.send


async def blocked_send(self, request, **kwargs):
    if request.url.host in STEAM_HOSTS:
        raise httpx.ConnectError(f"offline check: {request.url.host} is blocked")
    return await _real_send(self, request, **kwargs)


httpx.AsyncClient.send = blocked_send

created: list[TraceHooks] = []
_init = TraceHooks.__init__


def _record(self, *args, **kwargs):
    _init(self, *args, **kwargs)
    created.append(self)


TraceHooks.__init__ = _record


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = TestClient(app.app)
    sessions: dict[str, str] = {}
    failed = False
    for name, question, follow in RUNS:
        if not name.startswith("q"):  # the three README sample queries only
            continue
        body = {"message": question, "session_id": sessions.get(follow) if follow else None}
        t0 = time.monotonic()
        r = client.post("/chat", json=body)
        elapsed = round(time.monotonic() - t0, 1)
        data = r.json()
        if r.status_code != 200:
            print(f"== {name}: FAILED {r.status_code} {data}")
            failed = True
            continue
        sessions[name] = data["session_id"]
        cited = sorted(set(re.findall(r"\[(R\d+)\]", data["response"])), key=lambda x: int(x[1:]))
        missing = [c for c in cited if c not in data["evidence"]]
        snap = sum('"from_snapshot": true' in c["result"] for c in data["tool_calls"])
        chart = data.get("chart") or {}
        record = {
            "question": question,
            "seconds": elapsed,
            "tool_calls_per_agent": dict(Counter(c["agent"] for c in data["tool_calls"])),
            "snapshot_tool_calls": snap,
            "cited": cited,
            "cited_but_missing": missing,
            "chart_from_snapshot": chart.get("from_snapshot"),
            **data,
        }
        (OUT / f"{PREFIX}{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        print(f"== {name}: {elapsed}s, snapshot-backed tool calls {snap}, cited {len(cited)}, missing {missing}, "
              f"chart from snapshot {chart.get('from_snapshot')}")
        print(data["response"][:800], "\n")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
