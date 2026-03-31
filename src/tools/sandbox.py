"""Sandbox execution tools for dapr-swe agents.

Provides ``make_sandbox_tools(sandbox)`` which returns five ``@tool``-decorated
functions bound to the given sandbox backend via closures.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from dapr_agents.tool import tool

logger = logging.getLogger(__name__)


def make_sandbox_tools(sandbox: Any) -> list:
    """Create sandbox tool functions bound to *sandbox*.

    Args:
        sandbox: An OpenShell sandbox backend instance.

    Returns:
        List of ``@tool``-decorated callables.
    """

    @tool
    def execute(command: str, timeout: int = 300) -> str:
        """Run a shell command in the sandbox and return its output.

        Use this for any shell command: running tests, installing packages,
        inspecting processes, etc.

        Args:
            command: The shell command to execute.
            timeout: Maximum seconds to wait (default 300).

        Returns:
            Command output (stdout + stderr). Non-zero exit codes are appended.
        """
        try:
            result = sandbox.execute(command, timeout=timeout)
            output = result.output or ""
            if result.exit_code != 0:
                output += f"\n[exit code {result.exit_code}]"
            return output
        except Exception as exc:
            return f"Error executing command: {exc}"

    @tool
    def read_file(path: str) -> str:
        """Read a file from the sandbox and return its contents.

        Args:
            path: Absolute or relative path of the file to read.

        Returns:
            The file contents, or an error message.
        """
        try:
            result = sandbox.execute(f"cat {path}", timeout=30)
            if result.exit_code != 0:
                return f"Error reading {path}: {result.output}"
            return result.output
        except Exception as exc:
            return f"Error reading {path}: {exc}"

    @tool
    def write_file(path: str, content: str) -> str:
        """Write content to a file in the sandbox, creating parent directories as needed.

        Args:
            path: Absolute or relative path of the file to write.
            content: The content to write.

        Returns:
            Success or error message.
        """
        try:
            encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
            result = sandbox.execute(
                f"mkdir -p $(dirname {path}) && printf '%s' '{encoded}' | base64 -d > {path}",
                timeout=30,
            )
            if result.exit_code != 0:
                return f"Error writing {path}: {result.output}"
            return f"Successfully wrote {path}"
        except Exception as exc:
            return f"Error writing {path}: {exc}"

    @tool
    def list_directory(path: str = ".") -> str:
        """List directory contents in the sandbox.

        Args:
            path: Directory path to list (default: current directory).

        Returns:
            Detailed listing (ls -la output).
        """
        try:
            result = sandbox.execute(f"ls -la {path}", timeout=30)
            if result.exit_code != 0:
                return f"Error listing {path}: {result.output}"
            return result.output
        except Exception as exc:
            return f"Error listing {path}: {exc}"

    @tool
    def search_code(pattern: str, path: str = ".", file_glob: str = "") -> str:
        """Search for a regex pattern in the codebase using grep.

        Returns up to 200 matching lines with file paths and line numbers.

        Args:
            pattern: Regex pattern to search for.
            path: Directory or file to search in (default: current directory).
            file_glob: Optional glob to filter files (e.g. '*.py').

        Returns:
            Matching lines, or a message if nothing matched.
        """
        try:
            glob_flag = f"--include='{file_glob}'" if file_glob else ""
            cmd = f"grep -rn {glob_flag} '{pattern}' {path} | head -200"
            result = sandbox.execute(cmd, timeout=60)
            if result.exit_code == 1:
                return "No matches found."
            if result.exit_code != 0:
                return f"Error searching: {result.output}"
            return result.output or "No matches found."
        except Exception as exc:
            return f"Error searching: {exc}"

    return [execute, read_file, write_file, list_directory, search_code]


def _filter_tools(tools: list, names: set[str]) -> list:
    """Return only the tools whose ``__name__`` is in *names*."""
    return [t for t in tools if t.__name__ in names]


def make_readonly_sandbox_tools(sandbox: Any) -> list:
    """Create sandbox tools for the planning / exploration phase.

    Returns only read-oriented tools (no ``write_file``).

    Args:
        sandbox: An OpenShell sandbox backend instance.

    Returns:
        List of ``@tool``-decorated callables:
        [execute, read_file, list_directory, search_code].
    """
    return _filter_tools(
        make_sandbox_tools(sandbox),
        {"execute", "read_file", "list_directory", "search_code"},
    )


def make_test_tools(sandbox: Any) -> list:
    """Create sandbox tools for running tests, linters, and formatters.

    Args:
        sandbox: An OpenShell sandbox backend instance.

    Returns:
        List of ``@tool``-decorated callables:
        [execute, read_file, list_directory].
    """
    return _filter_tools(
        make_sandbox_tools(sandbox),
        {"execute", "read_file", "list_directory"},
    )
