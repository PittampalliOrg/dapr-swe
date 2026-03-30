"""OpenTelemetry tracing helpers for dapr-swe agent activities."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator


def _get_tracer():
    """Get the dapr-swe tracer (lazy import to handle missing OTEL gracefully)."""
    try:
        from opentelemetry import trace
        return trace.get_tracer("dapr-swe")
    except Exception:
        return None


@contextmanager
def trace_activity(name: str, attributes: dict[str, Any] | None = None) -> Generator:
    """Create a traced span for a workflow activity.

    Usage:
        with trace_activity("dapr-swe.plan", {"repo": "planner-agent"}):
            result = run_planner(...)
    """
    tracer = _get_tracer()
    if tracer is None:
        yield
        return

    with tracer.start_as_current_span(name, attributes=attributes or {}) as span:
        try:
            yield span
        except Exception as exc:
            span.set_attribute("error", True)
            span.set_attribute("error.message", str(exc))
            raise
