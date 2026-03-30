"""Pydantic models for GitHub webhook payloads."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GitHubUser(BaseModel):
    """GitHub user reference."""

    login: str
    id: int = 0


class GitHubLabel(BaseModel):
    """GitHub issue label."""

    name: str
    color: str = ""


class GitHubRepository(BaseModel):
    """GitHub repository reference."""

    full_name: str
    name: str
    owner: GitHubUser
    default_branch: str = "main"


class GitHubIssue(BaseModel):
    """GitHub issue payload."""

    number: int
    title: str
    body: str | None = None
    state: str = "open"
    labels: list[GitHubLabel] = Field(default_factory=list)
    user: GitHubUser | None = None


class GitHubComment(BaseModel):
    """GitHub issue comment."""

    id: int
    body: str = ""
    user: GitHubUser | None = None


class GitHubInstallation(BaseModel):
    """GitHub App installation reference."""

    id: int


class GitHubIssueEvent(BaseModel):
    """GitHub issues webhook event payload."""

    action: str
    issue: GitHubIssue
    repository: GitHubRepository
    sender: GitHubUser
    installation: GitHubInstallation | None = None
    comment: GitHubComment | None = None


class GitHubIssueCommentEvent(BaseModel):
    """GitHub issue_comment webhook event payload."""

    action: str
    issue: GitHubIssue
    comment: GitHubComment
    repository: GitHubRepository
    sender: GitHubUser
    installation: GitHubInstallation | None = None


class IssueContext(BaseModel):
    """Normalized issue context passed to the workflow."""

    owner: str
    repo: str
    issue_number: int
    title: str
    body: str = ""
    comments: list[dict[str, Any]] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    sender: str = ""
    installation_id: int = 0
