"""GitHub API tools for dapr-swe agents.

These tools make direct HTTP calls to the GitHub REST API and do not
require a sandbox backend.
"""

from __future__ import annotations

import httpx
from dapr_agents import tool
from pydantic import BaseModel, Field

GITHUB_API = "https://api.github.com"


# ---------------------------------------------------------------------------
# Tool: github_comment
# ---------------------------------------------------------------------------
class GitHubCommentArgs(BaseModel):
    message: str = Field(description="Comment body (Markdown supported)")
    repo_owner: str = Field(description="Repository owner (user or org)")
    repo_name: str = Field(description="Repository name")
    issue_number: int = Field(description="Issue or pull request number")
    token: str = Field(description="GitHub access token")


@tool(args_model=GitHubCommentArgs)
def github_comment(
    message: str,
    repo_owner: str,
    repo_name: str,
    issue_number: int,
    token: str,
) -> str:
    """Post a comment on a GitHub issue or pull request."""
    url = f"{GITHUB_API}/repos/{repo_owner}/{repo_name}/issues/{issue_number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"body": message}

    with httpx.Client(timeout=30) as client:
        resp = client.post(url, headers=headers, json=payload)

    if resp.status_code == 201:
        return f"Comment posted: {resp.json().get('html_url', 'ok')}"
    return f"Failed to post comment (HTTP {resp.status_code}): {resp.text}"


# ---------------------------------------------------------------------------
# Tool: create_pull_request
# ---------------------------------------------------------------------------
class CreatePullRequestArgs(BaseModel):
    title: str = Field(description="Pull request title")
    body: str = Field(description="Pull request description (Markdown)")
    head: str = Field(description="Head branch (the branch with changes)")
    base: str = Field(description="Base branch to merge into (e.g. 'main')")
    repo_owner: str = Field(description="Repository owner (user or org)")
    repo_name: str = Field(description="Repository name")
    token: str = Field(description="GitHub access token")


@tool(args_model=CreatePullRequestArgs)
def create_pull_request(
    title: str,
    body: str,
    head: str,
    base: str,
    repo_owner: str,
    repo_name: str,
    token: str,
) -> str:
    """Create a pull request on GitHub via the REST API."""
    url = f"{GITHUB_API}/repos/{repo_owner}/{repo_name}/pulls"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "title": title,
        "body": body,
        "head": head,
        "base": base,
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(url, headers=headers, json=payload)

    if resp.status_code == 201:
        pr_data = resp.json()
        return f"PR created: {pr_data.get('html_url', 'ok')} (#{pr_data.get('number')})"
    return f"Failed to create PR (HTTP {resp.status_code}): {resp.text}"


# ---------------------------------------------------------------------------
# Convenience list of all GitHub tools
# ---------------------------------------------------------------------------
github_tools = [github_comment, create_pull_request]
