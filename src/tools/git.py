"""Git operation tools for dapr-swe agents.

All git commands run inside the sandbox via the sandbox execute() tool.
Import ``set_sandbox`` from ``sandbox.py`` and call it before using these.
"""

from __future__ import annotations

from dapr_agents import tool
from pydantic import BaseModel, Field

from src.tools.sandbox import get_sandbox


# ---------------------------------------------------------------------------
# Tool: git_clone
# ---------------------------------------------------------------------------
class GitCloneArgs(BaseModel):
    repo_url: str = Field(description="HTTPS repository URL (e.g. https://github.com/owner/repo.git)")
    dest: str = Field(description="Destination directory inside the sandbox")
    token: str | None = Field(default=None, description="GitHub access token for private repos")


@tool(args_model=GitCloneArgs)
def git_clone(repo_url: str, dest: str, token: str | None = None) -> str:
    """Clone a git repository into the sandbox, optionally using token authentication."""
    sb = get_sandbox()

    clone_url = repo_url
    if token:
        # Inject token into URL: https://x-access-token:<token>@github.com/...
        clone_url = repo_url.replace(
            "https://github.com",
            f"https://x-access-token:{token}@github.com",
        )

    result = sb.execute(f"git clone {clone_url} {dest}", timeout=120)
    if result.exit_code != 0:
        return f"Clone failed: {result.output}"
    return f"Successfully cloned into {dest}"


# ---------------------------------------------------------------------------
# Tool: git_commit
# ---------------------------------------------------------------------------
class GitCommitArgs(BaseModel):
    message: str = Field(description="Commit message")
    repo_dir: str = Field(description="Path to the repository in the sandbox")


@tool(args_model=GitCommitArgs)
def git_commit(message: str, repo_dir: str) -> str:
    """Stage all changes and create a git commit in the sandbox."""
    sb = get_sandbox()

    # Configure git identity if not already set
    sb.execute(
        f"cd {repo_dir} && "
        "git config user.email 'dapr-swe[bot]@users.noreply.github.com' && "
        "git config user.name 'dapr-swe[bot]'",
        timeout=10,
    )

    # Stage all changes
    stage = sb.execute(f"cd {repo_dir} && git add -A", timeout=30)
    if stage.exit_code != 0:
        return f"Staging failed: {stage.output}"

    # Commit -- use a heredoc to safely pass the message
    import base64

    encoded_msg = base64.b64encode(message.encode("utf-8")).decode("ascii")
    commit = sb.execute(
        f"cd {repo_dir} && git commit -m \"$(printf '%s' '{encoded_msg}' | base64 -d)\"",
        timeout=30,
    )
    if commit.exit_code != 0:
        return f"Commit failed: {commit.output}"
    return commit.output


# ---------------------------------------------------------------------------
# Tool: git_push
# ---------------------------------------------------------------------------
class GitPushArgs(BaseModel):
    branch: str = Field(description="Branch name to push")
    repo_dir: str = Field(description="Path to the repository in the sandbox")
    token: str | None = Field(default=None, description="GitHub access token for push authentication")


@tool(args_model=GitPushArgs)
def git_push(branch: str, repo_dir: str, token: str | None = None) -> str:
    """Push a branch to the remote origin in the sandbox."""
    sb = get_sandbox()

    if token:
        # Update the remote URL to include the token
        get_url = sb.execute(f"cd {repo_dir} && git remote get-url origin", timeout=10)
        if get_url.exit_code == 0:
            origin_url = get_url.output.strip()
            authed_url = origin_url.replace(
                "https://github.com",
                f"https://x-access-token:{token}@github.com",
            )
            sb.execute(f"cd {repo_dir} && git remote set-url origin {authed_url}", timeout=10)

    result = sb.execute(f"cd {repo_dir} && git push -u origin {branch}", timeout=120)
    if result.exit_code != 0:
        return f"Push failed: {result.output}"
    return f"Successfully pushed {branch}"


# ---------------------------------------------------------------------------
# Tool: git_checkout_branch
# ---------------------------------------------------------------------------
class GitCheckoutBranchArgs(BaseModel):
    branch: str = Field(description="Branch name to create and checkout")
    repo_dir: str = Field(description="Path to the repository in the sandbox")


@tool(args_model=GitCheckoutBranchArgs)
def git_checkout_branch(branch: str, repo_dir: str) -> str:
    """Create and checkout a new branch in the sandbox repository."""
    sb = get_sandbox()
    result = sb.execute(f"cd {repo_dir} && git checkout -b {branch}", timeout=15)
    if result.exit_code != 0:
        return f"Checkout failed: {result.output}"
    return f"Checked out new branch: {branch}"


# ---------------------------------------------------------------------------
# Tool: git_diff
# ---------------------------------------------------------------------------
class GitDiffArgs(BaseModel):
    repo_dir: str = Field(description="Path to the repository in the sandbox")


@tool(args_model=GitDiffArgs)
def git_diff(repo_dir: str) -> str:
    """Get the diff of all uncommitted changes (staged and unstaged) in the sandbox."""
    sb = get_sandbox()
    result = sb.execute(f"cd {repo_dir} && git diff HEAD", timeout=30)
    if result.exit_code != 0:
        # Might be initial commit with no HEAD
        result = sb.execute(f"cd {repo_dir} && git diff --cached", timeout=30)
    return result.output if result.output.strip() else "No changes."


# ---------------------------------------------------------------------------
# Convenience list of all git tools
# ---------------------------------------------------------------------------
git_tools = [git_clone, git_commit, git_push, git_checkout_branch, git_diff]
