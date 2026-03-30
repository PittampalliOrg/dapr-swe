"""Sandbox execution tools for dapr-swe agents.

These tools wrap the OpenShell sandbox backend, providing shell execution,
file I/O, and search capabilities. Call ``set_sandbox(backend)`` before
using any tool so the module-level ``_sandbox`` reference is initialised.
"""

from __future__ import annotations

from typing import Any

from dapr_agents import tool
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Module-level sandbox reference -- set via set_sandbox() at workflow start
# ---------------------------------------------------------------------------
_sandbox: Any = None


def set_sandbox(backend: Any) -> None:
    """Bind the sandbox backend so all tools in this module can use it."""
    global _sandbox
    _sandbox = backend


def get_sandbox() -> Any:
    """Return the current sandbox backend or raise if not initialised."""
    if _sandbox is None:
        raise RuntimeError(
            "Sandbox not initialised. Call set_sandbox(backend) before invoking tools."
        )
    return _sandbox


# ---------------------------------------------------------------------------
# Tool: execute
# ---------------------------------------------------------------------------
class ExecuteArgs(BaseModel):
    command: str = Field(description="Shell command to execute in the sandbox")
    timeout: int = Field(default=300, description="Timeout in seconds")


@tool(args_model=ExecuteArgs)
def execute(command: str, timeout: int = 300) -> str:
    """Execute a shell command in the sandbox and return its output."""
    sb = get_sandbox()
    result = sb.execute(command, timeout=timeout)
    if result.exit_code != 0:
        return f"[exit {result.exit_code}] {result.output}"
    return result.output


# ---------------------------------------------------------------------------
# Tool: read_file
# ---------------------------------------------------------------------------
class ReadFileArgs(BaseModel):
    path: str = Field(description="Absolute or relative path of the file to read")


@tool(args_model=ReadFileArgs)
def read_file(path: str) -> str:
    """Read file contents from the sandbox."""
    sb = get_sandbox()
    result = sb.execute(f"cat {path}", timeout=30)
    if result.exit_code != 0:
        return f"Error reading {path}: {result.output}"
    return result.output


# ---------------------------------------------------------------------------
# Tool: write_file
# ---------------------------------------------------------------------------
class WriteFileArgs(BaseModel):
    path: str = Field(description="Absolute or relative path of the file to write")
    content: str = Field(description="Content to write to the file")


@tool(args_model=WriteFileArgs)
def write_file(path: str, content: str) -> str:
    """Write content to a file in the sandbox, creating parent directories as needed."""
    import base64

    sb = get_sandbox()
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    result = sb.execute(
        f"mkdir -p $(dirname {path}) && printf '%s' '{encoded}' | base64 -d > {path}",
        timeout=30,
    )
    if result.exit_code != 0:
        return f"Error writing {path}: {result.output}"
    return f"Successfully wrote {path}"


# ---------------------------------------------------------------------------
# Tool: glob_files
# ---------------------------------------------------------------------------
class GlobFilesArgs(BaseModel):
    pattern: str = Field(description="Glob pattern to match (e.g. '**/*.py')")
    path: str = Field(default=".", description="Root directory to search from")


@tool(args_model=GlobFilesArgs)
def glob_files(pattern: str, path: str = ".") -> str:
    """Find files matching a glob pattern in the sandbox."""
    sb = get_sandbox()
    # Use bash globstar for recursive patterns
    cmd = f"cd {path} && shopt -s globstar nullglob && printf '%s\\n' {pattern}"
    result = sb.execute(cmd, timeout=30)
    if result.exit_code != 0:
        return f"Error globbing: {result.output}"
    return result.output if result.output.strip() else "No files matched."


# ---------------------------------------------------------------------------
# Tool: grep_files
# ---------------------------------------------------------------------------
class GrepFilesArgs(BaseModel):
    pattern: str = Field(description="Regex pattern to search for")
    path: str = Field(default=".", description="Directory or file to search in")
    file_glob: str = Field(default="*", description="Glob to filter which files to search")


@tool(args_model=GrepFilesArgs)
def grep_files(pattern: str, path: str = ".", file_glob: str = "*") -> str:
    """Search for a regex pattern in files within the sandbox."""
    sb = get_sandbox()
    cmd = f"grep -rn --include='{file_glob}' '{pattern}' {path} | head -200"
    result = sb.execute(cmd, timeout=60)
    if result.exit_code == 1:
        return "No matches found."
    if result.exit_code != 0 and result.exit_code != 1:
        return f"Error searching: {result.output}"
    return result.output


# ---------------------------------------------------------------------------
# Convenience list of all sandbox tools
# ---------------------------------------------------------------------------
sandbox_tools = [execute, read_file, write_file, glob_files, grep_files]
