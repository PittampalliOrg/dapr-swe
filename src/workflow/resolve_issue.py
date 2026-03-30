"""Main Dapr Workflow: plan -> develop -> review -> PR."""

from __future__ import annotations

import dapr.ext.workflow as wf


def resolve_issue_workflow(ctx: wf.DaprWorkflowContext, input: dict) -> dict:
    """Orchestrate issue resolution: plan, develop, review, and open a PR.

    Args:
        ctx: Dapr workflow context (deterministic replay-safe).
        input: Dict with keys: owner, repo, issue_number, title, body, comments,
               sender, installation_id.

    Returns:
        Dict with pr_url and status.
    """
    # 1. Initialize: create sandbox, clone repo, read AGENTS.md
    context = yield ctx.call_activity(
        initialize_context,
        input=input,
    )

    # 2. Plan: explore codebase and produce implementation plan
    plan = yield ctx.call_activity(
        create_plan,
        input=context,
    )

    # 3. Develop: implement each step sequentially
    for i, step in enumerate(plan.get("steps", [])):
        step_input = {**context, "step": step, "step_index": i, "plan": plan}
        yield ctx.call_activity(
            implement_step,
            input=step_input,
        )

    # 4. Review: check the full diff
    review = yield ctx.call_activity(
        review_changes,
        input={**context, "plan": plan},
    )

    # 5. Commit and open PR
    pr_result = yield ctx.call_activity(
        commit_and_open_pr,
        input={**context, "plan": plan, "review": review},
    )

    # 6. Notify completion on the issue
    yield ctx.call_activity(
        notify_completion,
        input={**context, **pr_result, "review": review},
    )

    return pr_result


# Import activities so they are registered in the same module namespace.
# These are defined in activities.py and re-exported here for the workflow
# runtime registration.
from src.workflow.activities import (  # noqa: E402
    commit_and_open_pr,
    create_plan,
    implement_step,
    initialize_context,
    notify_completion,
    review_changes,
)

__all__ = [
    "resolve_issue_workflow",
    "initialize_context",
    "create_plan",
    "implement_step",
    "review_changes",
    "commit_and_open_pr",
    "notify_completion",
]
