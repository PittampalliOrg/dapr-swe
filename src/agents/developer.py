"""DeveloperAgent -- implements code changes according to a plan step."""

from __future__ import annotations

import logging
from typing import Any

from dapr_agents import DurableAgent
from dapr_agents.agents.configs import (
    AgentExecutionConfig,
    AgentProfileConfig,
)
from dapr_agents.tool import tool

from src.prompts.developer import DEVELOPER_SYSTEM_PROMPT, construct_developer_prompt
from src.sandbox.openshell import OpenShellBackend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sandbox-backed tools for code implementation
# ---------------------------------------------------------------------------


def make_developer_tools(sandbox: OpenShellBackend) -> list:
    """Create tool functions bound to a sandbox for code implementation."""

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
    def write_file(path: str, content: str) -> str:
        """Write content to a file in the sandbox. Always read before writing."""
        write_result = sandbox.write(path, content)
        if write_result.error:
            return f"Error: {write_result.error}"
        return f"Successfully wrote {path}"

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

    return [execute, read_file, write_file, list_directory, search_code]


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def create_developer_agent(
    sandbox: OpenShellBackend,
    working_dir: str = "/sandbox",
    agents_md: str = "",
    **kwargs: Any,
) -> DurableAgent:
    """Create a DurableAgent configured as a DeveloperAgent."""
    tools = make_developer_tools(sandbox)
    system_prompt = construct_developer_prompt(
        working_dir=working_dir,
        agents_md=agents_md,
    )

    return DurableAgent(
        profile=AgentProfileConfig(
            name="DeveloperAgent",
            role="Senior Software Engineer",
            goal="Implement code changes according to the plan step",
            system_prompt=system_prompt,
        ),
        tools=tools,
        execution=AgentExecutionConfig(max_iterations=40, tool_choice="auto"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Standalone runner using DurableAgent
# ---------------------------------------------------------------------------


def run_developer(
    sandbox: OpenShellBackend,
    step: dict,
    issue_context: dict,
    plan: dict,
    *,
    model_override: str | None = None,
    max_iterations: int | None = None,
    system_prompt_extra: str | None = None,
) -> dict:
    """Run the developer via DurableAgent for a single plan step.

    Returns a dict with keys: status, summary, files_changed.
    """
    from src.config import LLM_MODEL_ID
    from src.llm_providers import resolve_llm_client

    model = (
        model_override.removeprefix("anthropic/")
        if model_override
        else LLM_MODEL_ID.removeprefix("anthropic/")
    )

    working_dir = issue_context.get("working_dir", "/sandbox")
    agents_md = issue_context.get("agents_md", "")
    system_prompt = construct_developer_prompt(
        working_dir=working_dir,
        agents_md=agents_md,
    )
    if system_prompt_extra:
        system_prompt += "\n\n" + system_prompt_extra

    tools = make_developer_tools(sandbox)
    iteration_limit = max_iterations if max_iterations is not None else 50

    import asyncio
    from dapr_agents import Agent

    agent = Agent(
        name="DeveloperAgent",
        role="Software Developer",
        goal="Implement the assigned step from the plan",
        system_prompt=system_prompt,
        llm=resolve_llm_client(model),
        tools=tools,
        execution=AgentExecutionConfig(
            max_iterations=iteration_limit,
            tool_choice="auto",
        ),
    )

    result = asyncio.run(agent.run(_format_step_prompt(step, issue_context, plan)))

    # Normalize result into expected dict shape
    summary = result.content if result else "Developer completed step"
    return {
        "status": "completed",
        "summary": summary,
        "files_changed": step.get("files", []),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_step_prompt(step: dict, issue_context: dict, plan: dict) -> str:
    """Format a plan step into a user prompt for the developer."""
    parts = [
        f"## Original Issue: {issue_context.get('title', 'Untitled')}",
        "",
        issue_context.get("body", ""),
        "",
        f"## Plan Summary: {plan.get('summary', '')}",
        "",
        "## Your Current Step",
        "",
        f"**Title:** {step.get('title', '')}",
        f"**Description:** {step.get('description', '')}",
        f"**Files to modify:** {', '.join(step.get('files', []))}",
        f"**Complexity:** {step.get('complexity', 'medium')}",
        "",
        "Implement this step now. Read the relevant files first, then make "
        "the changes, and verify they work.",
    ]
    return "\n".join(parts)
