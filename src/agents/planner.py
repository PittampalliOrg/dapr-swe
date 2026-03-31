"""PlannerAgent -- explores a codebase and produces an implementation plan."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from dapr_agents import DurableAgent
from dapr_agents.agents.configs import (
    AgentExecutionConfig,
    AgentProfileConfig,
)
from dapr_agents.tool import tool

from src.prompts.planner import PLANNER_SYSTEM_PROMPT
from src.sandbox.openshell import OpenShellBackend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sandbox-backed tools the planner can use to explore the repo
# ---------------------------------------------------------------------------


def make_planner_tools(sandbox: OpenShellBackend) -> list:
    """Create tool functions bound to a sandbox instance."""

    @tool
    def execute(command: str, timeout: int = 300) -> str:
        """Run a shell command in the sandbox and return its output."""
        result = sandbox.execute(command, timeout=timeout)
        output = result.output or ""
        if result.exit_code != 0:
            output += f"\n[exit code {result.exit_code}]"
        return output

    @tool
    def read_file(path: str) -> str:
        """Read a file from the sandbox and return its contents."""
        result = sandbox.execute(f"cat {path}", timeout=30)
        if result.exit_code != 0:
            return f"Error reading {path}: {result.output}"
        return result.output

    @tool
    def list_directory(path: str = ".") -> str:
        """List directory contents in the sandbox."""
        result = sandbox.execute(f"ls -la {path}", timeout=30)
        return result.output

    @tool
    def search_code(pattern: str, path: str = ".", file_glob: str = "") -> str:
        """Search for a pattern in the codebase using grep."""
        glob_flag = f"--include='{file_glob}'" if file_glob else ""
        result = sandbox.execute(
            f"grep -rn {glob_flag} '{pattern}' {path} | head -100",
            timeout=60,
        )
        return result.output

    return [execute, read_file, list_directory, search_code]


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def create_planner_agent(
    sandbox: OpenShellBackend,
    **kwargs: Any,
) -> DurableAgent:
    """Create a DurableAgent configured as a PlannerAgent."""
    tools = make_planner_tools(sandbox)

    return DurableAgent(
        profile=AgentProfileConfig(
            name="PlannerAgent",
            role="Software Architect",
            goal="Analyze a codebase and produce a detailed implementation plan",
            system_prompt=PLANNER_SYSTEM_PROMPT,
        ),
        tools=tools,
        execution=AgentExecutionConfig(max_iterations=20, tool_choice="auto"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Standalone runner using DurableAgent
# ---------------------------------------------------------------------------


def run_planner(
    sandbox: OpenShellBackend,
    issue_context: dict,
    *,
    model_override: str | None = None,
    max_iterations: int | None = None,
    system_prompt_extra: str | None = None,
) -> dict:
    """Run the planner via DurableAgent and return the parsed plan."""
    from src.config import LLM_MODEL_ID
    from src.llm_providers import resolve_llm_client

    model = (
        model_override.removeprefix("anthropic/")
        if model_override
        else LLM_MODEL_ID.removeprefix("anthropic/")
    )

    system_prompt = PLANNER_SYSTEM_PROMPT
    if system_prompt_extra:
        system_prompt += "\n\n" + system_prompt_extra

    tools = make_planner_tools(sandbox)
    iteration_limit = max_iterations if max_iterations is not None else 30

    import asyncio
    from dapr_agents import Agent

    agent = Agent(
        name="PlannerAgent",
        role="Software Architect",
        goal="Analyze codebase and produce implementation plan as JSON",
        system_prompt=system_prompt,
        llm=resolve_llm_client(model),
        tools=tools,
        execution=AgentExecutionConfig(
            max_iterations=iteration_limit,
            tool_choice="auto",
        ),
    )

    task_prompt = _format_issue_prompt(issue_context)
    result = asyncio.run(agent.run(task_prompt))
    # result is AssistantMessage or None
    text = result.content if result else ""
    return _parse_plan(text)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_issue_prompt(issue_context: dict) -> str:
    """Format the issue context into a user prompt."""
    parts = [
        f"## Issue: {issue_context.get('title', 'Untitled')}",
        "",
        issue_context.get("body", "No description provided."),
        "",
    ]
    comments = issue_context.get("comments", [])
    if comments:
        parts.append("## Comments")
        for c in comments:
            # Wrap external comments in untrusted tags per open-swe security model
            parts.append(f"<untrusted-content user=\"{c.get('user', 'unknown')}\">")
            parts.append(c.get("body", ""))
            parts.append("</untrusted-content>")
            parts.append("")

    parts.append(
        f"Repository: {issue_context.get('owner', '')}/{issue_context.get('repo', '')}"
    )
    parts.append(f"Working directory: {issue_context.get('working_dir', '/sandbox')}")
    parts.append("")
    parts.append(
        "Explore the codebase using the tools, then produce your implementation "
        "plan as a JSON object. Remember: your final message must be ONLY valid JSON."
    )
    return "\n".join(parts)


def _parse_plan(text: str) -> dict:
    """Extract JSON plan from LLM text output."""
    if isinstance(text, dict):
        return text
    text = str(text).strip()
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

    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse plan JSON, creating actionable fallback")
    return {
        "summary": text[:500],
        "steps": [
            {
                "title": "Implement the requested changes",
                "description": (
                    "Analyze the issue requirements and implement them. "
                    "Read the relevant source files, make the necessary modifications, "
                    "and verify the changes work correctly. "
                    f"Context: {text[:1000]}"
                ),
                "files": [],
                "complexity": "medium",
            }
        ],
        "critical_files": [],
    }
