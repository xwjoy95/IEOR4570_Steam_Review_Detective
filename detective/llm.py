"""LLM setup shared by every agent: Gemini through LiteLLM, as in the course reference.

The Agents SDK runs the tool-calling loop, so there is no hand-written run_agent() here."""

import os
from pathlib import Path

from agents import ModelSettings, set_tracing_disabled
from agents.extensions.models.litellm_model import LitellmModel

# Traces upload to OpenAI by default, which needs an OpenAI key. We're on Gemini.
set_tracing_disabled(True)

# LiteLLM routes the call to Gemini. api_key is unused: Vertex AI uses your gcloud credentials.
MODEL = "vertex_ai/gemini-3.5-flash"  # the answer-writing model; the baseline script uses it too
SMART = LitellmModel(model=MODEL, api_key="unused")  # Clarifier, Lead, Writer
CHEAP = LitellmModel(model="vertex_ai/gemini-3.5-flash-lite", api_key="unused")  # Researchers

def resolve_vertex_project() -> str | None:
    """Use environment configuration or the same local project-file convention as Project 1."""
    configured = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("VERTEXAI_PROJECT")
    if configured:
        return configured
    root = Path(__file__).resolve().parents[1]
    for candidate in (root / "gcp-project.txt", root.parent / "gcp-project.txt"):
        if candidate.is_file():
            value = candidate.read_text(encoding="utf-8-sig").strip()
            if value:
                return value.splitlines()[0].strip()
    return None


# A stalled model call would hang the page: give up after 90 s, and retry twice.
SETTINGS = ModelSettings(extra_args={
    "vertex_project": resolve_vertex_project(),
    "vertex_location": os.environ.get("VERTEX_LOCATION") or os.environ.get("VERTEXAI_LOCATION", "global"),
    "timeout": 90,
    "num_retries": 2,
})
