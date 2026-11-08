"""One-command online verification: run every check that needs the real model and the live Steam
APIs, and print a PASS/FAIL line per check. Use this before the demo, after a redeploy, or to
confirm a teammate's machine is set up correctly.

Usage:
  uv run scripts/verify_online.py                # all checks (~10-15 min, spends model tokens)
  uv run scripts/verify_online.py steam vertex   # pre-flight only, fast
  uv run scripts/verify_online.py q1 vague       # any subset of the check names

Checks:
  steam     Steam endpoints reachable from this machine
  vertex    Vertex AI accepts a tiny completion with the project's credentials
  q1/q2/q3  the README's sample queries through POST /chat: cited, grounded, within the time budget,
            chart populated
  followup  a follow-up in the same session ("what about Chinese players?") continues the research
  vague     "Is it good?" gets a clarifying question back, not a made-up report
  stream    GET /run streams SSE and the done event carries tool_calls, evidence and chart

Results are saved to docs/verify/online_check.json; the exit code is 1 if any check failed.
"""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

STEAM_PROBES = [
    "https://store.steampowered.com/api/appdetails?appids=553850&cc=us",
    "https://store.steampowered.com/appreviewhistogram/553850?l=english",
    "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid=553850&count=1",
    "https://steamspy.com/api.php?request=appdetails&appid=553850",
]
OUT = ROOT / "docs" / "verify"
BUDGET_S = 300  # what a grader waits, and what Cloud Run should be set to

results: list[dict] = []


def record(name: str, ok: bool, detail: str) -> bool:
    results.append({"check": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name:<9} {detail}")
    return ok


def check_steam() -> bool:
    ok = True
    for url in STEAM_PROBES:
        host = url.split("/")[2]
        try:
            r = httpx.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (course project)"})
            if r.status_code in (403, 429) and "steamspy" in host:
                # SteamSpy rate-limits aggressively; the tools fall back to the bundled steamspy.json.
                ok &= record("steam", True, f"{host} -> HTTP {r.status_code} (rate-limited; tools fall back to the snapshot)")
            else:
                ok &= record("steam", r.status_code == 200, f"{host} -> HTTP {r.status_code}")
        except Exception as exc:
            ok = False
            record("steam", False, f"{host} -> {type(exc).__name__}: {str(exc)[:80]}")
    return ok


async def _vertex_ping() -> str:
    import logging

    import litellm

    litellm.suppress_debug_info = True  # no "Give Feedback" banners or tracebacks on a failed ping
    for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
        logging.getLogger(name).setLevel(logging.CRITICAL)

    from detective.llm import MODEL, resolve_vertex_project

    response = await litellm.acompletion(
        model=MODEL,
        messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        vertex_project=resolve_vertex_project(),
        vertex_location=os.environ.get("VERTEX_LOCATION") or os.environ.get("VERTEXAI_LOCATION", "global"),
        timeout=60,
        num_retries=1,
    )
    return response.choices[0].message.content or ""


def check_vertex() -> bool:
    t0 = time.monotonic()
    try:
        answer = asyncio.run(_vertex_ping())
        return record("vertex", bool(answer), f"model replied in {round(time.monotonic() - t0, 1)}s: {answer[:60]!r}")
    except Exception as exc:
        return record("vertex", False, f"{type(exc).__name__}: {str(exc)[:140]} (gcloud auth application-default login?)")


def audit_report(name: str, data: dict, elapsed: float, expect_researchers: bool = False) -> bool:
    """The checks every full report must satisfy."""
    ok = True
    response = data.get("response", "")
    tool_calls = data.get("tool_calls", [])
    evidence = data.get("evidence", {})
    cited = sorted(set(re.findall(r"\[(R\d+)\]", response)), key=lambda r: int(r[1:]))
    missing = [c for c in cited if c not in evidence]
    chart = data.get("chart") or {}
    researchers = sorted({c["agent"] for c in tool_calls if c["agent"] not in ("Clarifier", "Lead", "Writer")})

    ok &= record(name, elapsed < BUDGET_S, f"finished in {elapsed}s (budget {BUDGET_S}s)")
    ok &= record(name, len(response) > 400, f"report length {len(response)} chars")
    ok &= record(name, not missing, f"{len(cited)} citations, {len(missing)} missing")
    ok &= record(name, len(evidence) > 5, f"{len(evidence)} evidence items")
    ok &= record(name, bool(chart.get("series")),
                 f"chart: appid {chart.get('appid')}, {len(chart.get('series', []))} series points, "
                 f"{len(chart.get('patches', []))} patches, from_snapshot {chart.get('from_snapshot')}")
    if expect_researchers:
        ok &= record(name, bool(researchers), f"researchers ran: {researchers or 'none'}")
    return ok


def main(wanted: list[str]) -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    def want(name: str) -> bool:
        return not wanted or name in wanted

    if want("steam"):
        check_steam()
    if want("vertex"):
        check_vertex()

    model_checks = any(want(n) for n in ("q1", "q2", "q3", "followup", "vague", "stream"))
    vertex_ok = any(r["check"] == "vertex" and r["ok"] for r in results)
    if model_checks and not vertex_ok:
        if not want("vertex"):
            record("vertex", False, "model checks need Vertex AI; it was not reachable")
        print("Vertex AI is not reachable; skipping the model checks.")
        failed = [r for r in results if not r["ok"]]
        (OUT / "online_check.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        return 1 if failed else 0

    if model_checks:
        from fastapi.testclient import TestClient

        import app
        from e2e_runs import RUNS

        client = TestClient(app.app)
        sessions: dict[str, str] = {}

        samples = {"q1": "q1_spike", "q2": "q2_languages", "q3": "q3_veterans"}
        for short, run_name in samples.items():
            if not want(short):
                continue
            question = next(q for n, q, _ in RUNS if n == run_name)
            t0 = time.monotonic()
            r = client.post("/chat", json={"message": question})
            elapsed = round(time.monotonic() - t0, 1)
            if r.status_code != 200:
                record(short, False, f"HTTP {r.status_code}: {str(r.json().get('detail', ''))[:120]}")
                continue
            data = r.json()
            sessions[short] = data["session_id"]
            (OUT / f"{short}.json").write_text(json.dumps({"seconds": elapsed, **data}, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
            audit_report(short, data, elapsed)

        if want("followup"):
            base = sessions.get("q2") or sessions.get("q1")
            if base is None:
                record("followup", False, "run q1 or q2 first; the follow-up continues that session")
            else:
                t0 = time.monotonic()
                r = client.post("/chat", json={"message": "What about Chinese players compared to English players?",
                                               "session_id": base})
                elapsed = round(time.monotonic() - t0, 1)
                if r.status_code == 200:
                    data = r.json()
                    (OUT / "followup.json").write_text(json.dumps({"seconds": elapsed, **data}, ensure_ascii=False, indent=2),
                                                       encoding="utf-8")
                    ok = audit_report("followup", data, elapsed, expect_researchers=True)
                    ok &= record("followup", len(data["tool_calls"]) > 0,
                                 f"session {base[:8]}... continued with {len(data['tool_calls'])} tool calls")
                else:
                    record("followup", False, f"HTTP {r.status_code}: {str(r.json().get('detail', ''))[:120]}")

        if want("vague"):
            t0 = time.monotonic()
            r = client.post("/chat", json={"message": "Is it good?"})
            elapsed = round(time.monotonic() - t0, 1)
            if r.status_code == 200:
                data = r.json()
                researchers = [c["agent"] for c in data["tool_calls"] if c["agent"] != "Clarifier"]
                ok = record("vague", elapsed < 90 and len(data["response"]) < 600 and not researchers,
                            f"{elapsed}s, {len(data['response'])} chars, agents {researchers or 'only Clarifier'}; "
                            f"asks back: {data['response'][:80]!r}")
                (OUT / "vague.json").write_text(json.dumps({"seconds": elapsed, **data}, ensure_ascii=False, indent=2),
                                                encoding="utf-8")
            else:
                record("vague", False, f"HTTP {r.status_code}")

        if want("stream"):
            t0 = time.monotonic()
            events = []
            with client.stream("GET", "/run", params={"question": "Is it good?"}) as response:
                record("stream", response.status_code == 200, f"HTTP {response.status_code}")
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
            elapsed = round(time.monotonic() - t0, 1)
            done = [e for e in events if e["type"] == "done"]
            if done:
                d = done[-1]
                record("stream", "tool_calls" in d and "evidence" in d and "chart" in d,
                       f"{elapsed}s, {len(events)} events; done event has tool_calls={len(d.get('tool_calls', []))}, "
                       f"evidence={len(d.get('evidence', {}))}, chart={'chart' in d}")
            else:
                record("stream", False, f"no done event after {len(events)} events")

    failed = [r for r in results if not r["ok"]]
    (OUT / "online_check.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed; details in {OUT / 'online_check.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
