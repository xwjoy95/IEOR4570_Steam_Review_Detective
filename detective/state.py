"""One run's state, shared by every agent and tool through RunContextWrapper. The model never sees it."""

from dataclasses import dataclass, field

from agents import RunHooks

from evidence import EvidenceStore

# The Lead gets this many researchers in all. Prompts ask for restraint; code enforces it.
MAX_RESEARCHERS = 6
# Rounds of researchers: the first fan-out, plus at most two more for gaps.
MAX_ROUNDS = 3
# Tool calls one researcher may make; the tools themselves refuse past this.
MAX_TOOL_CALLS = 6


@dataclass
class Research:
    hooks: RunHooks
    store: EvidenceStore = field(default_factory=EvidenceStore)
    games: list[dict] = field(default_factory=list)  # [{"appid", "name"}], from the Clarifier
    researchers: int = 0  # how many have started, to number them
    lead_turns: int = 0  # how many times the Lead has called the model (TraceHooks counts them)
    round_turns: list[int] = field(default_factory=list)  # the Lead turns that sent researchers: one per round
    findings: list[dict] = field(default_factory=list)  # {"researcher", "round", "task", "findings", "cannot_answer"}
    chart: dict | None = None  # timeline for the page: {appid, granularity, as_of, from_snapshot,
    # series: [{period, up, down, pct_negative, ref}], patches: [{ref, id, date, title, url, official, kind}]}.
