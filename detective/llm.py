"""LLM setup shared by every agent: Gemini through LiteLLM, as in the course reference.

The Agents SDK runs the tool-calling loop, so there is no hand-written run_agent() here."""

from agents import ModelSettings, set_tracing_disabled
from agents.extensions.models.litellm_model import LitellmModel

# Traces upload to OpenAI by default, which needs an OpenAI key. We're on Gemini.
set_tracing_disabled(True)

# LiteLLM routes the call to Gemini. api_key is unused: Vertex AI uses your gcloud credentials.
SMART = LitellmModel(model="vertex_ai/gemini-3.5-flash", api_key="unused")  # Clarifier, Lead, Writer
CHEAP = LitellmModel(model="vertex_ai/gemini-3.5-flash-lite", api_key="unused")  # Researchers

# A stalled model call would hang the page: give up after 90 s, and retry twice.
SETTINGS = ModelSettings(extra_args={"vertex_location": "global", "timeout": 90, "num_retries": 2})
