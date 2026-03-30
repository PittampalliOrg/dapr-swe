"""Environment configuration for dapr-swe."""

from __future__ import annotations

import os

# GitHub App authentication
GITHUB_APP_ID: str = os.environ.get("GITHUB_APP_ID", "")
GITHUB_APP_PRIVATE_KEY: str = os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
GITHUB_APP_INSTALLATION_ID: str = os.environ.get("GITHUB_APP_INSTALLATION_ID", "")
GITHUB_WEBHOOK_SECRET: str = os.environ.get("GITHUB_WEBHOOK_SECRET", "")

# LLM provider
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

# OpenShell sandbox runtime
OPENSHELL_RUNTIME_URL: str = os.environ.get(
    "OPENSHELL_RUNTIME_URL",
    "http://openshell-agent-runtime.openshell.svc.cluster.local:8083",
)
OPENSHELL_COMMAND_TIMEOUT_MS: int = int(
    os.environ.get("OPENSHELL_COMMAND_TIMEOUT_MS", "600000")
)

# LLM model
LLM_MODEL_ID: str = os.environ.get("LLM_MODEL_ID", "anthropic/claude-sonnet-4-6")

# Repository defaults
DEFAULT_REPO_OWNER: str = os.environ.get("DEFAULT_REPO_OWNER", "PittampalliOrg")

# Dapr component names
DAPR_STATE_STORE: str = os.environ.get("DAPR_STATE_STORE", "dapr-swe-statestore")
DAPR_PUBSUB: str = os.environ.get("DAPR_PUBSUB", "pubsub")
WORKFLOW_EVENT_TOPIC: str = os.environ.get("WORKFLOW_EVENT_TOPIC", "workflow.stream")
ENABLE_WORKFLOW_EVENTS: bool = os.environ.get("ENABLE_WORKFLOW_EVENTS", "true").lower() == "true"

# Workflow-builder integration (Phase 2: DB registration)
WORKFLOW_BUILDER_BASE_URL: str = os.environ.get(
    "WORKFLOW_BUILDER_BASE_URL",
    "http://workflow-builder.workflow-builder.svc.cluster.local:3000",
)
WORKFLOW_BUILDER_INTERNAL_TOKEN: str = os.environ.get("WORKFLOW_BUILDER_INTERNAL_TOKEN", "")
WORKFLOW_BUILDER_WORKFLOW_ID: str = os.environ.get("WORKFLOW_BUILDER_WORKFLOW_ID", "")
