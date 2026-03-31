"""CodingAgent -- single DurableAgent with all tools, mirroring open-swe."""

from __future__ import annotations

import logging

from dapr_agents import DurableAgent
from dapr_agents.agents.configs import AgentExecutionConfig
from dapr_agents.agents.configs import WorkflowRetryPolicy

from src.llm_providers import resolve_llm_client
from src.prompts.coding_agent import construct_system_prompt
from src.tools.sandbox import make_sandbox_tools
from src.tools.github import make_github_tools
from src.tools.web import make_web_tools
from src.tools.linear import make_linear_tools
from src.tools.slack import make_slack_tools

logger = logging.getLogger(__name__)


def run_coding_agent(
    sandbox,
    issue_context: dict,
    *,
    model: str = "claude-opus-4-6",
    max_iterations: int = 1000,
):
    """Run the single CodingAgent loop end-to-end and return the result."""
    all_tools = (
        make_sandbox_tools(sandbox)
        + make_github_tools(sandbox, issue_context)
        + make_web_tools()
        + make_linear_tools(issue_context)
        + make_slack_tools(issue_context)
    )

    agent = DurableAgent(
        name="CodingAgent",
        role="Senior Software Engineer",
        goal="Resolve the assigned issue by implementing changes and opening a PR",
        system_prompt=construct_system_prompt(
            sandbox._working_directory,
            issue_context,
        ),
        llm=resolve_llm_client(model),
        tools=all_tools,
        execution=AgentExecutionConfig(
            max_iterations=max_iterations,
            tool_choice="auto",
        ),
        retry_policy=WorkflowRetryPolicy(
            max_attempts=3,
            initial_backoff_seconds=10,
            max_backoff_seconds=60,
            backoff_multiplier=2.0,
        ),
    )

    task = _format_task(issue_context)
    result = agent.run(task=task)
    return result


def _format_task(issue_context: dict) -> str:
    """Format issue context into the initial user message."""
    parts = [f"## Issue: {issue_context.get('title', 'Untitled')}"]
    parts.append("")
    parts.append(issue_context.get("body", "No description provided."))

    comments = issue_context.get("comments", [])
    if comments:
        parts.append("\n## Comments")
        for c in comments:
            parts.append(f"**{c.get('user', 'unknown')}:** {c.get('body', '')}")

    parts.append(
        f"\nRepository: {issue_context.get('owner', '')}/{issue_context.get('repo', '')}"
    )
    parts.append(f"Working directory: {issue_context.get('working_dir', '/sandbox')}")
    return "\n".join(parts)
