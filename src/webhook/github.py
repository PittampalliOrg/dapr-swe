"""GitHub webhook handler for dapr-swe."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

from src.config import GITHUB_WEBHOOK_SECRET
from src.webhook.models import (
    GitHubIssueCommentEvent,
    GitHubIssueEvent,
    IssueContext,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

# Label that triggers the bot (optional filter)
TRIGGER_LABEL = "dapr-swe"


# ---------------------------------------------------------------------------
# HMAC-SHA256 verification
# ---------------------------------------------------------------------------


def _verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify the GitHub webhook HMAC-SHA256 signature."""
    if not secret:
        logger.warning("GITHUB_WEBHOOK_SECRET not set, skipping signature verification")
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header("", alias="X-Hub-Signature-256"),
    x_github_event: str = Header("", alias="X-GitHub-Event"),
) -> dict[str, Any]:
    """Receive GitHub webhook events and start a Dapr Workflow for qualifying issues."""
    body = await request.body()

    # Verify signature
    if not _verify_signature(body, x_hub_signature_256, GITHUB_WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()

    # Route by event type
    if x_github_event == "issues":
        return await _handle_issue_event(payload)
    elif x_github_event == "issue_comment":
        return await _handle_issue_comment_event(payload)
    else:
        return {"status": "ignored", "event": x_github_event}


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


async def _handle_issue_event(payload: dict) -> dict[str, Any]:
    """Handle issues.opened and issues.labeled events."""
    event = GitHubIssueEvent.model_validate(payload)

    if event.action not in ("opened", "labeled"):
        return {"status": "ignored", "action": event.action}

    # Check for trigger label
    label_names = [label.name for label in event.issue.labels]
    if TRIGGER_LABEL not in label_names:
        return {"status": "ignored", "reason": f"missing '{TRIGGER_LABEL}' label"}

    issue_context = _build_issue_context(event)

    # Fetch existing comments
    issue_context.comments = await _fetch_issue_comments(
        owner=issue_context.owner,
        repo=issue_context.repo,
        issue_number=issue_context.issue_number,
        installation_id=issue_context.installation_id,
    )

    return await _start_workflow(issue_context)


async def _handle_issue_comment_event(payload: dict) -> dict[str, Any]:
    """Handle issue_comment.created events (for @dapr-swe mentions)."""
    event = GitHubIssueCommentEvent.model_validate(payload)

    if event.action != "created":
        return {"status": "ignored", "action": event.action}

    # Only trigger on comment body containing @dapr-swe or the trigger label
    comment_body = event.comment.body or ""
    label_names = [label.name for label in event.issue.labels]
    if "@dapr-swe" not in comment_body and TRIGGER_LABEL not in label_names:
        return {"status": "ignored", "reason": "no trigger found in comment"}

    issue_context = IssueContext(
        owner=event.repository.owner.login,
        repo=event.repository.name,
        issue_number=event.issue.number,
        title=event.issue.title,
        body=event.issue.body or "",
        labels=label_names,
        sender=event.sender.login,
        installation_id=event.installation.id if event.installation else 0,
        comments=[
            {
                "user": event.comment.user.login if event.comment.user else "unknown",
                "body": event.comment.body,
            }
        ],
    )

    return await _start_workflow(issue_context)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_issue_context(event: GitHubIssueEvent) -> IssueContext:
    """Convert a GitHubIssueEvent into an IssueContext."""
    return IssueContext(
        owner=event.repository.owner.login,
        repo=event.repository.name,
        issue_number=event.issue.number,
        title=event.issue.title,
        body=event.issue.body or "",
        labels=[label.name for label in event.issue.labels],
        sender=event.sender.login,
        installation_id=event.installation.id if event.installation else 0,
    )


async def _fetch_issue_comments(
    owner: str,
    repo: str,
    issue_number: int,
    installation_id: int,
) -> list[dict[str, Any]]:
    """Fetch existing comments on an issue from the GitHub API."""
    from src.integrations.github_app import get_github_app_installation_token

    token = await get_github_app_installation_token()
    if not token:
        return []

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                params={"per_page": 30},
            )
            resp.raise_for_status()
            raw_comments = resp.json()
    except Exception:
        logger.exception("Failed to fetch issue comments")
        return []

    return [
        {
            "user": c.get("user", {}).get("login", "unknown"),
            "body": c.get("body", ""),
        }
        for c in raw_comments
    ]


async def _start_workflow(issue_context: IssueContext) -> dict[str, Any]:
    """Start a Dapr Workflow for the given issue context."""
    from dapr.ext.workflow import DaprWorkflowClient

    # Deterministic instance ID based on repo + issue number
    instance_id = (
        f"resolve-{issue_context.owner}-{issue_context.repo}-{issue_context.issue_number}"
    )

    try:
        from src.workflow.resolve_issue import resolve_issue_workflow

        wf_client = DaprWorkflowClient()
        try:
            wf_client.schedule_new_workflow(
                workflow=resolve_issue_workflow,
                input=issue_context.model_dump(),
                instance_id=instance_id,
            )
            logger.info(
                "Started workflow %s for %s/%s#%d",
                instance_id,
                issue_context.owner,
                issue_context.repo,
                issue_context.issue_number,
            )
            return {
                "status": "started",
                "instance_id": instance_id,
                "issue": f"{issue_context.owner}/{issue_context.repo}#{issue_context.issue_number}",
            }
        except Exception:
            # Check if the workflow instance already exists
            existing = wf_client.get_workflow_state(
                instance_id=instance_id, fetch_payloads=False
            )
            if existing is None:
                raise
            # Instance already exists, just log and continue
            logger.info("Workflow instance %s already exists, skipping", instance_id)
            return {
                "status": "already_running",
                "instance_id": instance_id,
                "issue": f"{issue_context.owner}/{issue_context.repo}#{issue_context.issue_number}",
            }
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to start workflow %s", instance_id)
        raise HTTPException(status_code=500, detail="Failed to start workflow")
