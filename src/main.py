"""FastAPI application for dapr-swe."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from dapr.ext.workflow import WorkflowRuntime
from fastapi import FastAPI, Request

from src.actions import ACTION_HANDLERS
from src.webhook.github import router as github_router
from src.workflow.resolve_issue import (
    commit_and_open_pr,
    create_plan,
    implement_step,
    initialize_context,
    notify_completion,
    resolve_issue_workflow,
    review_changes,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Workflow runtime setup
# ---------------------------------------------------------------------------

_workflow_runtime: WorkflowRuntime | None = None


def _create_workflow_runtime() -> WorkflowRuntime:
    """Create and register the Dapr Workflow runtime."""
    runtime = WorkflowRuntime()

    # Register workflow
    runtime.register_workflow(resolve_issue_workflow)

    # Register activities
    runtime.register_activity(initialize_context)
    runtime.register_activity(create_plan)
    runtime.register_activity(implement_step)
    runtime.register_activity(review_changes)
    runtime.register_activity(commit_and_open_pr)
    runtime.register_activity(notify_completion)

    return runtime


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage workflow runtime lifecycle."""
    global _workflow_runtime
    _workflow_runtime = _create_workflow_runtime()
    _workflow_runtime.start()
    logger.info("Dapr Workflow runtime started")
    try:
        yield
    finally:
        if _workflow_runtime is not None:
            try:
                _workflow_runtime.shutdown()
            except Exception:
                logger.debug("Error shutting down workflow runtime", exc_info=True)
            logger.info("Dapr Workflow runtime stopped")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="dapr-swe",
    description="Distributed coding agent on Dapr Workflows with OpenShell sandboxes",
    version="0.1.0",
    lifespan=lifespan,
)

# Register routes
app.include_router(github_router)


@app.get("/healthz")
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy", "service": "dapr-swe"}


@app.get("/readyz")
async def readiness_check() -> dict:
    """Readiness check endpoint."""
    ready = _workflow_runtime is not None
    return {"status": "ready" if ready else "not_ready", "service": "dapr-swe"}


@app.post("/execute")
async def execute_action(request: Request) -> dict:
    """Handle action requests from workflow-builder function-router."""
    body = await request.json()
    function_slug = body.get("function_slug", "")
    input_data = body.get("input", {})
    node_outputs = body.get("node_outputs", {})

    handler = ACTION_HANDLERS.get(function_slug)
    if not handler:
        return {"success": False, "error": f"Unknown action: {function_slug}", "data": {}}

    try:
        import asyncio

        # Run handler in thread to avoid blocking uvicorn's event loop
        # (handlers use sync httpx and may call asyncio.run internally)
        result = await asyncio.to_thread(handler, input_data, node_outputs)
        return result
    except Exception as e:
        logger.exception("Action handler %s failed", function_slug)
        return {"success": False, "error": str(e), "data": {}}
