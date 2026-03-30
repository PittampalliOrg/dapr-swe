"""PlannerAgent -- explores a codebase and produces an implementation plan."""

from __future__ import annotations

import json
import logging
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
# Standalone runner (for activity context without full DurableAgent lifecycle)
# ---------------------------------------------------------------------------


def run_planner(sandbox: OpenShellBackend, issue_context: dict) -> dict:
    """Run the planner loop and return the parsed plan.

    This is a simplified runner for use inside a workflow activity where we
    do not need the full DurableAgent pub/sub lifecycle.  It calls the
    Anthropic API directly via httpx as a fallback.
    """
    import httpx

    from src.config import ANTHROPIC_API_KEY, LLM_MODEL_ID

    tools_defs = _build_tool_specs()
    sandbox_tools = {t.__name__: t for t in _make_raw_tools(sandbox)}
    messages: list[dict] = [
        {
            "role": "user",
            "content": _format_issue_prompt(issue_context),
        },
    ]

    model = LLM_MODEL_ID.removeprefix("anthropic/")

    for _ in range(30):
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": 16384,
            "system": PLANNER_SYSTEM_PROMPT,
            "messages": messages,
            "tools": tools_defs,
        }

        with httpx.Client(timeout=300) as client:
            resp = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        # Collect assistant content blocks
        content_blocks = data.get("content", [])
        messages.append({"role": "assistant", "content": content_blocks})

        # Check if there are tool calls
        tool_use_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
        if not tool_use_blocks:
            # Final text response -- extract plan JSON
            text = "".join(
                b.get("text", "") for b in content_blocks if b.get("type") == "text"
            )
            return _parse_plan(text)

        # Execute tool calls and collect results
        tool_results: list[dict] = []
        for block in tool_use_blocks:
            fn_name = block["name"]
            fn_args = block.get("input", {})
            tool_fn = sandbox_tools.get(fn_name)
            if tool_fn is None:
                result_text = f"Unknown tool: {fn_name}"
            else:
                try:
                    result_text = tool_fn(**fn_args)
                except Exception as exc:
                    result_text = f"Tool error: {exc}"
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": str(result_text),
                }
            )

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError("Planner did not produce a plan within the iteration limit")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_raw_tools(sandbox: OpenShellBackend) -> list:
    """Create plain callables (not dapr_agents @tool decorated) for the standalone runner."""

    def execute(command: str, timeout: int = 300) -> str:
        result = sandbox.execute(command, timeout=timeout)
        output = result.output or ""
        if result.exit_code != 0:
            output += f"\n[exit code {result.exit_code}]"
        return output

    def read_file(path: str) -> str:
        result = sandbox.execute(f"cat {path}", timeout=30)
        if result.exit_code != 0:
            return f"Error reading {path}: {result.output}"
        return result.output

    def list_directory(path: str = ".") -> str:
        result = sandbox.execute(f"ls -la {path}", timeout=30)
        return result.output

    def search_code(pattern: str, path: str = ".", file_glob: str = "") -> str:
        glob_flag = f"--include='{file_glob}'" if file_glob else ""
        result = sandbox.execute(
            f"grep -rn {glob_flag} '{pattern}' {path} | head -100",
            timeout=60,
        )
        return result.output

    return [execute, read_file, list_directory, search_code]


def _build_tool_specs() -> list[dict]:
    """Anthropic API tool definitions for the planner."""
    return [
        {
            "name": "execute",
            "description": "Run a shell command in the sandbox and return its output.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 300)",
                        "default": 300,
                    },
                },
                "required": ["command"],
            },
        },
        {
            "name": "read_file",
            "description": "Read a file from the sandbox and return its contents.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "list_directory",
            "description": "List directory contents in the sandbox.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path (default '.')",
                        "default": ".",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "search_code",
            "description": "Search for a pattern in the codebase using grep.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Grep pattern"},
                    "path": {
                        "type": "string",
                        "description": "Directory to search (default '.')",
                        "default": ".",
                    },
                    "file_glob": {
                        "type": "string",
                        "description": "File glob filter (e.g. '*.py')",
                        "default": "",
                    },
                },
                "required": ["pattern"],
            },
        },
    ]


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
            parts.append(f"**{c.get('user', 'unknown')}:** {c.get('body', '')}")
            parts.append("")

    parts.append(
        f"Repository: {issue_context.get('owner', '')}/{issue_context.get('repo', '')}"
    )
    parts.append(f"Working directory: {issue_context.get('working_dir', '/sandbox')}")
    parts.append("")
    parts.append(
        "Explore the codebase and produce an implementation plan as described "
        "in your system prompt."
    )
    return "\n".join(parts)


def _parse_plan(text: str) -> dict:
    """Extract JSON plan from LLM text output."""
    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON within the text
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse plan JSON, returning raw text as summary")
    return {
        "summary": text[:500],
        "steps": [
            {
                "title": "Implement changes",
                "description": text,
                "files": [],
                "complexity": "medium",
            }
        ],
        "critical_files": [],
    }
