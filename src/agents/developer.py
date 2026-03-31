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
# Standalone runner (for activity context)
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
    """Run the developer loop for a single plan step.

    Returns a dict with keys: status, summary, files_changed.

    Parameters
    ----------
    model_override : str | None
        LLM model to use instead of the configured ``LLM_MODEL_ID``.
    max_iterations : int | None
        Maximum agentic loop iterations (default 50).
    system_prompt_extra : str | None
        Extra text appended to the system prompt.
    """
    import httpx

    from src.config import ANTHROPIC_API_KEY, LLM_MODEL_ID

    working_dir = issue_context.get("working_dir", "/sandbox")
    agents_md = issue_context.get("agents_md", "")
    system_prompt = construct_developer_prompt(
        working_dir=working_dir,
        agents_md=agents_md,
    )
    if system_prompt_extra:
        system_prompt = system_prompt + "\n\n" + system_prompt_extra

    tools_defs = _build_tool_specs()
    sandbox_tools = {t.__name__: t for t in _make_raw_tools(sandbox)}
    messages: list[dict] = [
        {
            "role": "user",
            "content": _format_step_prompt(step, issue_context, plan),
        },
    ]

    model = (model_override.removeprefix("anthropic/") if model_override else
             LLM_MODEL_ID.removeprefix("anthropic/"))

    iteration_limit = max_iterations if max_iterations is not None else 50

    for _ in range(iteration_limit):
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": 16384,
            "system": system_prompt,
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

        content_blocks = data.get("content", [])
        messages.append({"role": "assistant", "content": content_blocks})

        tool_use_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
        if not tool_use_blocks:
            text = "".join(
                b.get("text", "") for b in content_blocks if b.get("type") == "text"
            )
            return {
                "status": "completed",
                "summary": text,
                "files_changed": step.get("files", []),
            }

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

    return {
        "status": "max_iterations_reached",
        "summary": "Developer hit iteration limit",
        "files_changed": step.get("files", []),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_raw_tools(sandbox: OpenShellBackend) -> list:
    """Create plain callables for the standalone runner."""

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

    def write_file(path: str, content: str) -> str:
        write_result = sandbox.write(path, content)
        if write_result.error:
            return f"Error: {write_result.error}"
        return f"Successfully wrote {path}"

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

    return [execute, read_file, write_file, list_directory, search_code]


def _build_tool_specs() -> list[dict]:
    """Anthropic API tool definitions for the developer."""
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
            "name": "write_file",
            "description": "Write content to a file in the sandbox. Always read the file first.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path"},
                    "content": {"type": "string", "description": "File content to write"},
                },
                "required": ["path", "content"],
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
