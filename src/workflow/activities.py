"""Workflow activity implementations for resolve_issue_workflow."""

from __future__ import annotations

import logging

import httpx
from dapr.ext.workflow import WorkflowActivityContext

from src.config import GITHUB_WEBHOOK_SECRET
from src.integrations.github_app import get_github_app_installation_token
from src.sandbox.openshell import OpenShellBackend, create_openshell_sandbox

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Initialize context
# ---------------------------------------------------------------------------


def initialize_context(ctx: WorkflowActivityContext, input: dict) -> dict:
    """Create sandbox, clone the repo, and read AGENTS.md.

    Input keys: owner, repo, issue_number, title, body, comments, sender,
                installation_id.
    Returns enriched context dict with sandbox_id, working_dir, agents_md, github_token.
    """
    import asyncio

    owner = input["owner"]
    repo = input["repo"]

    # Get GitHub token
    token = asyncio.get_event_loop().run_until_complete(
        get_github_app_installation_token()
    )
    if not token:
        raise RuntimeError("Failed to obtain GitHub App installation token")

    # Create sandbox
    sandbox = create_openshell_sandbox()
    sandbox_id = sandbox.id
    working_dir = f"/sandbox/{repo}"

    # Clone repository
    clone_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
    result = sandbox.execute(
        f"git clone --depth=50 {clone_url} {working_dir}",
        timeout=120,
    )
    if result.exit_code != 0:
        raise RuntimeError(f"Failed to clone repository: {result.output}")

    # Store credentials for later push
    sandbox.execute(
        f"echo 'https://x-access-token:{token}@github.com' > /tmp/.git-credentials",
        timeout=10,
    )

    # Configure git identity
    sandbox.execute(
        f'cd {working_dir} && '
        'git config user.email "dapr-swe[bot]@users.noreply.github.com" && '
        'git config user.name "dapr-swe[bot]"',
        timeout=10,
    )

    # Read AGENTS.md if present
    agents_md = ""
    agents_result = sandbox.execute(f"cat {working_dir}/AGENTS.md 2>/dev/null", timeout=10)
    if agents_result.exit_code == 0 and agents_result.output.strip():
        agents_md = agents_result.output.strip()

    return {
        **input,
        "sandbox_id": sandbox_id,
        "working_dir": working_dir,
        "agents_md": agents_md,
        "github_token": token,
    }


# ---------------------------------------------------------------------------
# 2. Create plan
# ---------------------------------------------------------------------------


def create_plan(ctx: WorkflowActivityContext, input: dict) -> dict:
    """Run the PlannerAgent to produce an implementation plan."""
    from src.agents.planner import run_planner

    sandbox = _reconnect_sandbox(input["sandbox_id"])
    plan = run_planner(sandbox, input)
    logger.info("Plan created: %s", plan.get("summary", ""))
    return plan


# ---------------------------------------------------------------------------
# 3. Implement step
# ---------------------------------------------------------------------------


def implement_step(ctx: WorkflowActivityContext, input: dict) -> dict:
    """Run the DeveloperAgent for a single plan step."""
    from src.agents.developer import run_developer

    sandbox = _reconnect_sandbox(input["sandbox_id"])
    step = input["step"]
    plan = input.get("plan", {})
    step_index = input.get("step_index", 0)

    logger.info("Implementing step %d: %s", step_index, step.get("title", ""))

    result = run_developer(
        sandbox=sandbox,
        step=step,
        issue_context=input,
        plan=plan,
    )

    logger.info("Step %d result: %s", step_index, result.get("status", "unknown"))
    return result


# ---------------------------------------------------------------------------
# 4. Review changes
# ---------------------------------------------------------------------------


def review_changes(ctx: WorkflowActivityContext, input: dict) -> dict:
    """Run the ReviewerAgent on the full diff."""
    from src.agents.reviewer import run_reviewer

    sandbox = _reconnect_sandbox(input["sandbox_id"])
    working_dir = input["working_dir"]

    # Get the full diff
    diff_result = sandbox.execute(
        f"cd {working_dir} && git diff HEAD",
        timeout=60,
    )
    diff = diff_result.output or ""

    # If no unstaged changes, check for staged changes or commits ahead of origin
    if not diff.strip():
        diff_result = sandbox.execute(
            f"cd {working_dir} && git diff origin/main...HEAD",
            timeout=60,
        )
        diff = diff_result.output or ""

    plan = input.get("plan", {})
    review = run_reviewer(diff=diff, issue_context=input, plan=plan)
    logger.info("Review: approved=%s", review.get("approved"))
    return review


# ---------------------------------------------------------------------------
# 5. Commit and open PR
# ---------------------------------------------------------------------------


def commit_and_open_pr(ctx: WorkflowActivityContext, input: dict) -> dict:
    """Stage all changes, create a branch, commit, push, and open a GitHub PR."""
    sandbox = _reconnect_sandbox(input["sandbox_id"])
    working_dir = input["working_dir"]
    owner = input["owner"]
    repo = input["repo"]
    issue_number = input["issue_number"]
    title = input.get("title", "Untitled issue")
    plan = input.get("plan", {})
    token = input["github_token"]

    branch_name = f"dapr-swe/issue-{issue_number}"

    # Create branch, stage, commit
    sandbox.execute(
        f"cd {working_dir} && "
        f"git checkout -b {branch_name} && "
        "git add -A && "
        f'git commit -m "fix: {title} [closes #{issue_number}]" --allow-empty',
        timeout=60,
    )

    # Push
    push_result = sandbox.execute(
        f"cd {working_dir} && git push -u origin {branch_name}",
        timeout=120,
    )
    if push_result.exit_code != 0:
        logger.error("Push failed: %s", push_result.output)
        return {"status": "error", "error": push_result.output, "pr_url": ""}

    # Open PR via GitHub API
    pr_body = _build_pr_body(plan, issue_number)
    pr_data = {
        "title": f"fix: {title} [closes #{issue_number}]",
        "head": branch_name,
        "base": "main",
        "body": pr_body,
        "draft": True,
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"https://api.github.com/repos/{owner}/{repo}/pulls",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json=pr_data,
        )

    if resp.status_code == 201:
        pr_url = resp.json().get("html_url", "")
        logger.info("PR created: %s", pr_url)
        return {"status": "success", "pr_url": pr_url}
    elif resp.status_code == 422:
        # PR may already exist
        logger.warning("PR creation returned 422: %s", resp.text)
        return {"status": "already_exists", "pr_url": "", "detail": resp.text}
    else:
        logger.error("PR creation failed: %s %s", resp.status_code, resp.text)
        return {"status": "error", "error": resp.text, "pr_url": ""}


# ---------------------------------------------------------------------------
# 6. Notify completion
# ---------------------------------------------------------------------------


def notify_completion(ctx: WorkflowActivityContext, input: dict) -> dict:
    """Post a comment on the GitHub issue summarizing the result."""
    owner = input["owner"]
    repo = input["repo"]
    issue_number = input["issue_number"]
    token = input.get("github_token", "")
    pr_url = input.get("pr_url", "")
    review = input.get("review", {})
    status = input.get("status", "")

    if status == "success" and pr_url:
        body = (
            f"I've opened a draft PR with the implementation: {pr_url}\n\n"
            f"**Review:** {'Approved' if review.get('approved') else 'Needs changes'}\n"
            f"**Feedback:** {review.get('feedback', 'N/A')}"
        )
    elif status == "error":
        body = (
            "I encountered an error while trying to resolve this issue.\n\n"
            f"**Error:** {input.get('error', 'Unknown error')}"
        )
    else:
        body = "I attempted to resolve this issue but was unable to open a PR."

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

    return {"notified": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reconnect_sandbox(sandbox_id: str) -> OpenShellBackend:
    """Reconnect to an existing sandbox by its ID."""
    return create_openshell_sandbox(sandbox_id=sandbox_id)


def _build_pr_body(plan: dict, issue_number: int) -> str:
    """Build a PR description from the plan."""
    summary = plan.get("summary", "Automated implementation")
    steps = plan.get("steps", [])

    parts = [
        "## Summary",
        "",
        summary,
        "",
        f"Closes #{issue_number}",
        "",
    ]

    if steps:
        parts.append("## Changes")
        parts.append("")
        for step in steps:
            parts.append(f"- **{step.get('title', '')}**: {step.get('description', '')}")
        parts.append("")

    parts.append("---")
    parts.append("*Generated by dapr-swe*")

    return "\n".join(parts)
