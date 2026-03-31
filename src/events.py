"""Shared event publishing helpers for workflow-builder integration."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import json

import httpx
from dapr.clients import DaprClient

from src.config import (
    DAPR_PUBSUB,
    ENABLE_WORKFLOW_EVENTS,
    WORKFLOW_BUILDER_BASE_URL,
    WORKFLOW_BUILDER_INTERNAL_TOKEN,
    WORKFLOW_BUILDER_WORKFLOW_ID,
    WORKFLOW_EVENT_TOPIC,
)

logger = logging.getLogger(__name__)


def publish_event(event_type: str, data: dict) -> None:
    """Publish a workflow event to the shared NATS JetStream topic.

    Best-effort — failures are logged but never block the workflow.
    """
    if not ENABLE_WORKFLOW_EVENTS:
        return
    payload = {
        "type": event_type,
        "source": "dapr-swe",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "datacontenttype": "application/json",
        "data": data,
    }
    try:
        with DaprClient() as client:
            client.publish_event(
                pubsub_name=DAPR_PUBSUB,
                topic_name=WORKFLOW_EVENT_TOPIC,
                data=json.dumps(payload),
                data_content_type="application/json",
            )
    except Exception:
        logger.debug("Failed to publish event %s (best-effort)", event_type)


def register_execution(instance_id: str, issue_context: dict) -> str | None:
    """Register a workflow execution in workflow-builder's DB.

    Calls the BFF execute API which creates a DB record. The orchestrator
    may start a duplicate workflow that fails — this is expected and harmless.
    The DB record persists regardless and gets updated by our status events.

    Returns the execution ID if successful, None otherwise.
    Best-effort — failures don't block the workflow.
    """
    if not WORKFLOW_BUILDER_INTERNAL_TOKEN or not WORKFLOW_BUILDER_WORKFLOW_ID:
        return None

    issue_ref = f"{issue_context.get('owner')}/{issue_context.get('repo')}#{issue_context.get('issue_number')}"

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(
                f"{WORKFLOW_BUILDER_BASE_URL}/api/internal/agent/workflows/execute",
                headers={
                    "X-Internal-Token": WORKFLOW_BUILDER_INTERNAL_TOKEN,
                    "Content-Type": "application/json",
                },
                json={
                    "workflowId": WORKFLOW_BUILDER_WORKFLOW_ID,
                    "triggerData": {
                        "source": "dapr-swe",
                        "issue": issue_ref,
                        "title": issue_context.get("title", ""),
                        "owner": issue_context.get("owner", ""),
                        "repo": issue_context.get("repo", ""),
                        "issue_number": issue_context.get("issue_number", 0),
                        "daprInstanceId": instance_id,
                    },
                },
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                execution_id = data.get("executionId", "")
                logger.info("Registered execution %s in workflow-builder", execution_id)
                return execution_id
    except Exception:
        logger.debug("Failed to register execution in workflow-builder (best-effort)")
    return None


def update_execution_status(execution_id: str, phase: str, progress: int, status: str = "running") -> None:
    """Update the workflow execution status in workflow-builder DB via pub/sub event.

    Uses the same event format as the workflow-orchestrator's publish_phase_changed.
    Best-effort.
    """
    if not execution_id or not ENABLE_WORKFLOW_EVENTS:
        return
    publish_event("workflow.phase.changed", {
        "executionId": execution_id,
        "workflowId": WORKFLOW_BUILDER_WORKFLOW_ID,
        "phase": phase,
        "progress": progress,
        "status": status,
        "source": "dapr-swe",
    })


def post_agent_event(execution_id: str, event_type: str, data: dict) -> None:
    """Post an agent event to workflow-builder for execution tracking.

    Best-effort — failures don't block the workflow.
    """
    if not execution_id or not WORKFLOW_BUILDER_INTERNAL_TOKEN:
        return
    try:
        with httpx.Client(timeout=5) as client:
            client.post(
                f"{WORKFLOW_BUILDER_BASE_URL}/api/internal/agent-events",
                headers={
                    "X-Internal-Token": WORKFLOW_BUILDER_INTERNAL_TOKEN,
                    "Content-Type": "application/json",
                },
                json={
                    "workflowExecutionId": execution_id,
                    "events": [{
                        "event_type": event_type,
                        "phase": data.get("phase", ""),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        **{k: v for k, v in data.items() if k != "phase"},
                    }],
                },
            )
    except Exception:
        logger.debug("Failed to post agent event %s (best-effort)", event_type)


def post_issue_comment(owner: str, repo: str, issue_number: int, body: str, token: str) -> None:
    """Post a comment on a GitHub issue.

    Best-effort — failures are logged but never block the workflow.
    """
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"body": body},
        )

    if resp.status_code not in (200, 201):
        logger.error("Failed to post issue comment: %s %s", resp.status_code, resp.text)
