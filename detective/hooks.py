"""Hooks that record every tool call for the /chat response's tool_calls list.

Extends the reference's PanelHooks, so the context panel and /run's activity events keep working.
"""

import json
import time

from context_panel import AgentContexts, PanelHooks

MAX_RESULT_CHARS = 2_000  # tool results can be hundreds of reviews: keep the response small


def _arguments(context) -> dict:
    # SDK 0.24: for function tools the hook's context is a ToolContext carrying the raw JSON arguments.
    raw = getattr(context, "tool_arguments", None)
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {"raw": raw}


class TraceHooks(PanelHooks):
    def __init__(self, contexts: AgentContexts | None = None, emit=None):
        super().__init__(contexts or AgentContexts(), emit or (lambda event: None))
        self.tool_calls: list[dict] = []  # [{agent, name, args, result}], in the order they finished
        self.timings: dict[str, list[float]] = {}  # agent -> [started, ended] (monotonic seconds), to check parallelism

    async def on_agent_start(self, context, agent):
        self.timings.setdefault(agent.name, [time.monotonic(), None])
        await super().on_agent_start(context, agent)

    async def on_agent_end(self, context, agent, output):
        self.timings.setdefault(agent.name, [time.monotonic(), None])[1] = time.monotonic()
        await super().on_agent_end(context, agent, output)

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        # Count the Lead's turns: research() uses them to tell rounds apart.
        if agent.name == "Lead" and hasattr(context.context, "lead_turns"):
            context.context.lead_turns += 1
        await super().on_llm_start(context, agent, system_prompt, input_items)

    async def on_tool_end(self, context, agent, tool, result):
        # agent.name is the numbered clone's ("Review Analyst 2"), so parallel researchers stay apart.
        self.tool_calls.append(
            {"agent": agent.name, "name": tool.name, "args": _arguments(context), "result": str(result)[:MAX_RESULT_CHARS]}
        )
