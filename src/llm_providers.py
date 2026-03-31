"""LLM provider resolution — direct SDK clients for DurableAgent.

Uses OpenAIChatClient with provider-specific base_url since the Dapr
Conversation API's Anthropic component has a tool_choice format bug.
OpenAIChatClient is auto-instrumented by DaprAgentsInstrumentor's LLMWrapper.

When the Conversation API bug is fixed, switch back to DaprChatClient
for provider decoupling.
"""
from __future__ import annotations

import logging
import os

from dapr_agents.llm.chat import ChatClientBase
from dapr_agents.llm.openai import OpenAIChatClient

logger = logging.getLogger(__name__)

# Anthropic supports the OpenAI SDK via base_url
# See: https://docs.anthropic.com/en/api/openai-sdk
_ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")


def resolve_llm_client(model_name: str) -> ChatClientBase:
    """Return an LLM client for the given model name.

    Uses OpenAIChatClient (auto-instrumented by DaprAgentsInstrumentor)
    with provider-specific base_url.
    """
    model_lower = model_name.lower().removeprefix("anthropic/").removeprefix("openai/")

    if model_lower.startswith(("claude", "anthropic")):
        return OpenAIChatClient(
            api_key=_ANTHROPIC_API_KEY,
            base_url="https://api.anthropic.com/v1/",
            model=model_lower,
            timeout=600,
        )
    elif model_lower.startswith(("gpt", "o1", "o3", "o4")):
        return OpenAIChatClient(
            api_key=_OPENAI_API_KEY,
            model=model_lower,
            timeout=600,
        )
    else:
        # Default to Anthropic
        return OpenAIChatClient(
            api_key=_ANTHROPIC_API_KEY,
            base_url="https://api.anthropic.com/v1/",
            model=model_lower or "claude-sonnet-4-6",
            timeout=600,
        )
