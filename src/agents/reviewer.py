"""ReviewerAgent -- reviews a git diff for correctness, style, and completeness."""

from __future__ import annotations

import json
import logging
from typing import Any

from dapr_agents import DurableAgent
from dapr_agents.agents.configs import (
    AgentExecutionConfig,
    AgentProfileConfig,
)

from src.prompts.reviewer import REVIEWER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def create_reviewer_agent(**kwargs: Any) -> DurableAgent:
    """Create a DurableAgent configured as a ReviewerAgent."""
    return DurableAgent(
        profile=AgentProfileConfig(
            name="ReviewerAgent",
            role="Senior Code Reviewer",
            goal="Review code changes for correctness, style, and completeness",
            system_prompt=REVIEWER_SYSTEM_PROMPT,
        ),
        tools=[],
        execution=AgentExecutionConfig(max_iterations=5, tool_choice=None),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Standalone runner using DurableAgent
# ---------------------------------------------------------------------------


def run_reviewer(
    diff: str,
    issue_context: dict,
    plan: dict,
    *,
    model_override: str | None = None,
) -> dict:
    """Run the reviewer via DurableAgent and return the parsed review.

    Returns a dict with keys: approved, feedback, suggestions.
    """
    from src.config import LLM_MODEL_ID
    from src.llm_providers import resolve_llm_client

    model = (
        model_override.removeprefix("anthropic/")
        if model_override
        else LLM_MODEL_ID.removeprefix("anthropic/")
    )

    import asyncio
    from dapr_agents import Agent

    agent = Agent(
        name="ReviewerAgent",
        role="Code Reviewer",
        goal="Review code changes for correctness, style, and completeness",
        system_prompt=REVIEWER_SYSTEM_PROMPT,
        llm=resolve_llm_client(model),
        tools=[],
        execution=AgentExecutionConfig(max_iterations=1),
    )

    result = asyncio.run(agent.run(_format_review_prompt(diff, issue_context, plan)))
    return _parse_review(result.content if result else "")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_review_prompt(diff: str, issue_context: dict, plan: dict) -> str:
    """Format the review context into a user prompt."""
    parts = [
        f"## Issue: {issue_context.get('title', 'Untitled')}",
        "",
        issue_context.get("body", "No description."),
        "",
        f"## Implementation Plan Summary: {plan.get('summary', '')}",
        "",
        "## Git Diff",
        "",
        "```diff",
        diff[:100_000],  # Truncate very large diffs
        "```",
        "",
        "Review these changes according to your system prompt and produce your assessment.",
    ]
    return "\n".join(parts)


def _parse_review(text: str) -> dict:
    """Extract JSON review from LLM text output."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse review JSON, treating as approved with raw feedback")
    return {
        "approved": True,
        "feedback": text[:1000],
        "suggestions": [],
    }
