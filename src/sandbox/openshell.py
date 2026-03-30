"""OpenShell sandbox backend implementation.

Uses the openshell-agent-runtime workspace HTTP API to create and manage
sandboxed execution environments via OpenShell.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from src.config import OPENSHELL_COMMAND_TIMEOUT_MS, OPENSHELL_RUNTIME_URL

# Workspace creation timeout
DEFAULT_WORKSPACE_TIMEOUT_S = 120  # 2 minutes


# ---------------------------------------------------------------------------
# Simple protocol (replaces deepagents dependency)
# ---------------------------------------------------------------------------


@dataclass
class ExecuteResult:
    """Result of executing a command in a sandbox."""

    output: str
    exit_code: int
    truncated: bool = False


@dataclass
class FileDownloadResult:
    """Result of downloading a file from a sandbox."""

    path: str
    content: bytes
    error: str | None = None


@dataclass
class FileUploadResult:
    """Result of uploading a file to a sandbox."""

    path: str
    error: str | None = None


@dataclass
class WriteResult:
    """Result of writing a file in a sandbox."""

    path: str | None = None
    error: str | None = None


class SandboxBackend(Protocol):
    """Minimal sandbox backend protocol."""

    @property
    def id(self) -> str: ...

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResult: ...

    def write(self, file_path: str, content: str) -> None: ...

    def cleanup(self) -> None: ...


# ---------------------------------------------------------------------------
# OpenShell implementation
# ---------------------------------------------------------------------------


class OpenShellBackend:
    """OpenShell backend implementation conforming to SandboxBackend protocol.

    Communicates with the openshell-agent-runtime HTTP API to execute commands
    in isolated OpenShell sandbox pods.
    """

    def __init__(
        self,
        workspace_ref: str,
        base_url: str,
        sandbox_name: str,
        working_directory: str = "/sandbox",
    ) -> None:
        self._workspace_ref = workspace_ref
        self._base_url = base_url
        self._sandbox_name = sandbox_name
        self._working_directory = working_directory
        self._default_timeout_ms = OPENSHELL_COMMAND_TIMEOUT_MS

    @property
    def id(self) -> str:
        """Unique identifier for the sandbox backend."""
        return self._workspace_ref

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResult:
        """Execute a command in the OpenShell sandbox.

        Args:
            command: Full shell command string to execute.
            timeout: Maximum time in seconds to wait for the command to complete.
                If None, uses the default timeout.

        Returns:
            ExecuteResult with combined output, exit code, and truncation flag.
        """
        timeout_ms = (timeout * 1000) if timeout is not None else self._default_timeout_ms

        payload = {
            "workspaceRef": self._workspace_ref,
            "command": command,
            "cwd": self._working_directory,
            "timeoutMs": timeout_ms,
        }

        with httpx.Client(timeout=max(timeout_ms / 1000 + 30, 60)) as client:
            response = client.post(f"{self._base_url}/api/workspaces/command", json=payload)
            response.raise_for_status()
            data = response.json()

        stdout = data.get("stdout") or ""
        stderr = data.get("stderr") or ""
        output = stdout
        if stderr:
            output += "\n" + stderr if output else stderr

        return ExecuteResult(
            output=output,
            exit_code=data.get("exitCode", 1),
            truncated=data.get("timedOut", False),
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        """Write content to a file in the sandbox.

        Uses base64 encoding via printf to avoid shell escaping issues
        with tokens and special characters.
        """
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        result = self.execute(
            f"mkdir -p $(dirname {file_path}) && printf '%s' '{encoded}' | base64 -d > {file_path}",
            timeout=30,
        )
        if result.exit_code != 0:
            return WriteResult(error=f"Failed to write file '{file_path}': {result.output}")
        # Verify the write succeeded
        verify = self.execute(f"test -f {file_path} && echo ok || echo fail", timeout=5)
        if "fail" in verify.output:
            return WriteResult(error=f"Write verification failed for '{file_path}'")
        return WriteResult(path=file_path)

    def download_files(self, paths: list[str]) -> list[FileDownloadResult]:
        """Download files from the sandbox via shell commands."""
        results: list[FileDownloadResult] = []
        for path in paths:
            result = self.execute(f"cat {path}", timeout=30)
            if result.exit_code == 0:
                results.append(FileDownloadResult(path=path, content=result.output.encode()))
            else:
                results.append(FileDownloadResult(path=path, content=b"", error=result.output))
        return results

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResult]:
        """Upload files to the sandbox via shell commands."""
        results: list[FileUploadResult] = []
        for path, content in files:
            encoded = base64.b64encode(content).decode()
            result = self.execute(
                f"mkdir -p $(dirname {path}) && echo {encoded} | base64 -d > {path}",
                timeout=30,
            )
            if result.exit_code == 0:
                results.append(FileUploadResult(path=path))
            else:
                results.append(FileUploadResult(path=path, error=result.output))
        return results

    def cleanup(self) -> None:
        """Clean up the workspace session."""
        payload = {"workspaceRef": self._workspace_ref}
        try:
            with httpx.Client(timeout=30) as client:
                client.post(f"{self._base_url}/api/workspaces/cleanup", json=payload)
        except Exception:
            pass


def create_openshell_sandbox(
    sandbox_id: str | None = None,
) -> OpenShellBackend:
    """Create or reconnect to an OpenShell sandbox.

    Uses the openshell-agent-runtime workspace API to provision a sandbox
    environment. Sandboxes persist across multiple agent invocations.

    Args:
        sandbox_id: Optional existing workspace ref to reconnect to.
            If None, creates a new workspace.

    Returns:
        OpenShellBackend instance.
    """
    base_url = OPENSHELL_RUNTIME_URL.rstrip("/")

    if sandbox_id:
        # Reconnect to existing workspace
        backend = OpenShellBackend(
            workspace_ref=sandbox_id,
            base_url=base_url,
            sandbox_name=sandbox_id,
        )
        # Verify connectivity
        try:
            result = backend.execute("echo ready", timeout=10)
            if result.exit_code == 0:
                return backend
        except Exception:
            pass
        # If reconnection fails, fall through to create new workspace

    # Create new workspace
    payload = {
        "workspaceRef": sandbox_id or "",
        "rootPath": "/sandbox",
        "commandTimeoutMs": OPENSHELL_COMMAND_TIMEOUT_MS,
        "requiredCapabilities": ["bash", "git"],
    }

    with httpx.Client(timeout=DEFAULT_WORKSPACE_TIMEOUT_S) as client:
        response = client.post(f"{base_url}/api/workspaces/profile", json=payload)
        response.raise_for_status()
        data = response.json()

    workspace_profile = data.get("workspaceProfile", {})
    workspace_ref = workspace_profile.get("workspaceRef") or data.get("workspaceRef", "")
    sandbox_name = workspace_profile.get("sandboxName") or data.get("sandboxName", workspace_ref)
    working_directory = workspace_profile.get("workingDirectory") or "/sandbox"

    backend = OpenShellBackend(
        workspace_ref=workspace_ref,
        base_url=base_url,
        sandbox_name=sandbox_name,
        working_directory=working_directory,
    )

    # Verify sandbox is ready
    for _ in range(DEFAULT_WORKSPACE_TIMEOUT_S // 2):
        try:
            result = backend.execute("echo ready", timeout=5)
            if result.exit_code == 0:
                break
        except Exception:
            pass
        time.sleep(2)
    else:
        msg = f"OpenShell sandbox failed to become ready within {DEFAULT_WORKSPACE_TIMEOUT_S} seconds"
        raise RuntimeError(msg)

    # Configure sandbox environment for git operations:
    # 1. Disable SSL verification (sandbox images may lack CA certs)
    # 2. Set up global credential helper so git push works across execute() calls
    # 3. Try to install ca-certificates for proper SSL (re-enable if successful)
    backend.execute(
        "git config --global http.sslVerify false "
        "&& git config --global credential.helper 'store --file=/tmp/.git-credentials' "
        "&& (apt-get update -qq && apt-get install -y -qq ca-certificates 2>/dev/null "
        "&& git config --global http.sslVerify true || true)",
        timeout=60,
    )

    return backend
