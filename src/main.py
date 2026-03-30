"""FastAPI application for dapr-swe."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dapr.ext.workflow import WorkflowRuntime
from fastapi import FastAPI, Request

# ---------------------------------------------------------------------------
# OpenTelemetry initialization (must happen before FastAPI app creation)
# ---------------------------------------------------------------------------

_otel_ready = False

def _init_otel() -> None:
    """Initialize OpenTelemetry tracing if OTEL_EXPORTER_OTLP_ENDPOINT is set."""
    global _otel_ready
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        logging.getLogger(__name__).info("OTEL_EXPORTER_OTLP_ENDPOINT not set, skipping tracing")
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({
            "service.name": os.environ.get("OTEL_SERVICE_NAME", "dapr-swe"),
            "service.namespace": "workflow-builder",
        })
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        ))
        trace.set_tracer_provider(provider)

        # Auto-instrument httpx (FastAPI will be instrumented after app creation)
        HTTPXClientInstrumentor().instrument()

        _otel_ready = True
        logging.getLogger(__name__).info("OpenTelemetry tracing initialized → %s", endpoint)
    except Exception as exc:
        logging.getLogger(__name__).warning("OpenTelemetry init failed: %s", exc)

_init_otel()

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

# Instrument FastAPI app with OTEL (must happen after app creation)
if _otel_ready:
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
        logger.info("FastAPI OTEL instrumentation applied")
    except Exception as exc:
        logger.warning("FastAPI OTEL instrumentation failed: %s", exc)


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
    """Handle action requests from workflow-builder function-router.

    Accepts multiple payload formats:
    - Direct: {"function_slug": "dapr-swe/plan", "input": {...}, "node_outputs": {...}}
    - Orchestrator: {"input": {"actionType": "dapr-swe/plan", ...}, "node_outputs": {...}}
    - Function-router: full body with function_slug or input.actionType
    """
    body = await request.json()

    # Resolve function slug from multiple possible locations
    function_slug = (
        body.get("function_slug")
        or body.get("function_id")
        or (body.get("input", {}) or {}).get("actionType", "")
    )
    input_data = body.get("input", body)  # Fall back to full body as input
    node_outputs = body.get("node_outputs", {})

    handler = ACTION_HANDLERS.get(function_slug)
    if not handler:
        return {"success": False, "error": f"Unknown action: {function_slug}", "data": {}}

    try:
        import asyncio

        from src.tracing import trace_activity

        # Run handler in thread with tracing span
        def _run():
            with trace_activity(f"dapr-swe.execute.{function_slug}", {
                "dapr_swe.action": function_slug,
                "dapr_swe.repo": (input_data.get("owner", "") + "/" + input_data.get("repo", "")).strip("/"),
            }):
                return handler(input_data, node_outputs)

        result = await asyncio.to_thread(_run)
        return result
    except Exception as e:
        logger.exception("Action handler %s failed", function_slug)
        return {"success": False, "error": str(e), "data": {}}
