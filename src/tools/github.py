"""GitHub tools for dapr-swe agents.

Provides ``make_github_tools(sandbox, issue_context)`` which returns
``@tool``-decorated functions for committing, creating PRs, commenting on
issues, and managing PR reviews via the GitHub REST API.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from dapr_agents.tool import tool

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo_api_url(owner: str, name: str) -> str:
    return f"{GITHUB_API}/repos/{owner}/{name}"


def make_github_tools(sandbox: Any, issue_context: dict[str, Any]) -> list:
    """Create GitHub tool functions bound to *sandbox* and *issue_context*.

    ``issue_context`` should contain at minimum:
        - ``github_token``  – a valid GitHub access token
        - ``repo_owner``    – repository owner (user or org)
        - ``repo_name``     – repository name
        - ``issue_number``  – issue or PR number (int)

    For PR review tools the context should also contain:
        - ``pr_number``     – pull request number (falls back to issue_number)

    Args:
        sandbox: OpenShell sandbox backend for git operations.
        issue_context: Dict with repo / token / issue metadata.

    Returns:
        List of ``@tool``-decorated callables.
    """
    token = issue_context.get("github_token", "")
    repo_owner = issue_context.get("repo_owner") or issue_context.get("owner", "")
    repo_name = issue_context.get("repo_name") or issue_context.get("repo", "")
    issue_number = issue_context.get("issue_number")
    pr_number = issue_context.get("pr_number") or issue_number
    working_dir = issue_context.get("working_dir") or issue_context.get("repo_dir", f"/sandbox/{repo_name}")
    repo_dir = working_dir
    branch_name = issue_context.get("branch_name", "")

    base_url = _repo_api_url(repo_owner, repo_name)

    # ------------------------------------------------------------------
    # commit_and_open_pr
    # ------------------------------------------------------------------
    @tool
    def commit_and_open_pr(
        title: str,
        body: str,
        commit_message: str | None = None,
    ) -> str:
        """Commit all current changes and open a GitHub Pull Request.

        Call this when your work is done and you want to submit for review.
        Before calling, ensure you have reviewed your changes and run any
        linting/formatting commands.

        The PR title should follow: <type>: <description> [closes <ID>]
        Where type is one of: fix, feat, chore, ci.

        Args:
            title: PR title (keep under 70 characters).
            body: PR description with ## Description and ## Test Plan sections.
            commit_message: Optional commit message. Defaults to the PR title.

        Returns:
            JSON string with success, pr_url, and error fields.
        """
        try:
            if not token:
                return json.dumps({"success": False, "error": "Missing github_token in issue_context"})

            # Check for changes
            status = sandbox.execute(f"cd {repo_dir} && git status --porcelain", timeout=15)
            unpushed = sandbox.execute(
                f"cd {repo_dir} && git log @{{u}}..HEAD --oneline 2>/dev/null || echo ''",
                timeout=15,
            )
            has_changes = bool(status.output and status.output.strip())
            has_unpushed = bool(unpushed.output and unpushed.output.strip())

            if not has_changes and not has_unpushed:
                return json.dumps({"success": False, "error": "No changes detected"})

            # Determine target branch
            import time as _time
            target = branch_name or f"dapr-swe/issue-{issue_number or 'patch'}-{int(_time.time())}"

            # Ensure we're on the target branch
            current = sandbox.execute(f"cd {repo_dir} && git rev-parse --abbrev-ref HEAD", timeout=10)
            if current.output.strip() != target:
                checkout = sandbox.execute(f"cd {repo_dir} && git checkout -B {target}", timeout=15)
                if checkout.exit_code != 0:
                    return json.dumps({"success": False, "error": f"Checkout failed: {checkout.output}"})

            # Configure identity
            sandbox.execute(
                f"cd {repo_dir} && "
                "git config user.email 'dapr-swe[bot]@users.noreply.github.com' && "
                "git config user.name 'dapr-swe[bot]'",
                timeout=10,
            )

            # Stage & commit
            if has_changes:
                sandbox.execute(f"cd {repo_dir} && git add -A", timeout=30)
                import base64

                msg = commit_message or title
                encoded = base64.b64encode(msg.encode()).decode()
                commit_res = sandbox.execute(
                    f"cd {repo_dir} && git commit -m \"$(printf '%s' '{encoded}' | base64 -d)\"",
                    timeout=30,
                )
                if commit_res.exit_code != 0:
                    return json.dumps({"success": False, "error": f"Commit failed: {commit_res.output}"})

            # Push (inject token into remote URL)
            get_url = sandbox.execute(f"cd {repo_dir} && git remote get-url origin", timeout=10)
            origin = get_url.output.strip()
            authed = origin.replace("https://github.com", f"https://x-access-token:{token}@github.com")
            sandbox.execute(f"cd {repo_dir} && git remote set-url origin {authed}", timeout=10)
            push = sandbox.execute(f"cd {repo_dir} && git push -u origin {target}", timeout=120)
            # Restore clean remote URL
            sandbox.execute(f"cd {repo_dir} && git remote set-url origin {origin}", timeout=10)

            if push.exit_code != 0:
                return json.dumps({"success": False, "error": f"Push failed: {push.output}"})

            # Determine base branch
            with httpx.Client(timeout=30) as client:
                resp = client.get(f"{base_url}", headers=_github_headers(token))
                base_branch = resp.json().get("default_branch", "main") if resp.status_code == 200 else "main"

            # Create PR (or find existing)
            with httpx.Client(timeout=30) as client:
                pr_resp = client.post(
                    f"{base_url}/pulls",
                    headers=_github_headers(token),
                    json={"title": title, "body": body, "head": target, "base": base_branch},
                )
                if pr_resp.status_code == 201:
                    pr_data = pr_resp.json()
                    return json.dumps({
                        "success": True,
                        "pr_url": pr_data.get("html_url"),
                        "pr_existing": False,
                    })
                elif pr_resp.status_code == 422:
                    # PR may already exist
                    existing = client.get(
                        f"{base_url}/pulls",
                        headers=_github_headers(token),
                        params={"head": f"{repo_owner}:{target}", "state": "open"},
                    )
                    if existing.status_code == 200 and existing.json():
                        pr_data = existing.json()[0]
                        return json.dumps({
                            "success": True,
                            "pr_url": pr_data.get("html_url"),
                            "pr_existing": True,
                        })
                    return json.dumps({"success": False, "error": f"PR creation failed: {pr_resp.text}"})
                else:
                    return json.dumps({"success": False, "error": f"HTTP {pr_resp.status_code}: {pr_resp.text}"})

        except Exception as exc:
            logger.exception("commit_and_open_pr failed")
            return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})

    # ------------------------------------------------------------------
    # github_comment
    # ------------------------------------------------------------------
    @tool
    def github_comment(message: str) -> str:
        """Post a comment on the current GitHub issue or pull request.

        Args:
            message: Comment body (Markdown supported).

        Returns:
            JSON string with success status and comment URL or error.
        """
        try:
            if not token:
                return json.dumps({"success": False, "error": "Missing github_token"})
            if not message.strip():
                return json.dumps({"success": False, "error": "Message cannot be empty"})
            if not issue_number:
                return json.dumps({"success": False, "error": "No issue_number in context"})

            url = f"{base_url}/issues/{issue_number}/comments"
            with httpx.Client(timeout=30) as client:
                resp = client.post(url, headers=_github_headers(token), json={"body": message})

            if resp.status_code == 201:
                return json.dumps({"success": True, "url": resp.json().get("html_url", "")})
            return json.dumps({"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"})
        except Exception as exc:
            return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})

    # ------------------------------------------------------------------
    # PR Review tools
    # ------------------------------------------------------------------
    @tool
    def list_pr_reviews() -> str:
        """List all reviews on the current pull request.

        Returns:
            JSON string with success status and reviews list.
        """
        try:
            if not token or not pr_number:
                return json.dumps({"success": False, "error": "Missing token or pr_number"})
            url = f"{base_url}/pulls/{pr_number}/reviews"
            with httpx.Client(timeout=30) as client:
                resp = client.get(url, headers=_github_headers(token))
            if resp.status_code != 200:
                return json.dumps({"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"})
            return json.dumps({"success": True, "reviews": resp.json()})
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})

    @tool
    def get_pr_review(review_id: int) -> str:
        """Get a specific review on the current pull request.

        Args:
            review_id: The ID of the review to retrieve.

        Returns:
            JSON string with success status and review data.
        """
        try:
            if not token or not pr_number:
                return json.dumps({"success": False, "error": "Missing token or pr_number"})
            url = f"{base_url}/pulls/{pr_number}/reviews/{review_id}"
            with httpx.Client(timeout=30) as client:
                resp = client.get(url, headers=_github_headers(token))
            if resp.status_code != 200:
                return json.dumps({"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"})
            return json.dumps({"success": True, "review": resp.json()})
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})

    @tool
    def create_pr_review(
        event: str,
        body: str | None = None,
        comments: str | None = None,
    ) -> str:
        """Create a review on the current pull request.

        Args:
            event: Review action — one of APPROVE, REQUEST_CHANGES, or COMMENT.
            body: Review body text (required for APPROVE/REQUEST_CHANGES).
            comments: Optional JSON-encoded list of review comment objects.
                Each object: {path, body, line?, side?, start_line?, start_side?}.

        Returns:
            JSON string with success status and created review data.
        """
        try:
            if not token or not pr_number:
                return json.dumps({"success": False, "error": "Missing token or pr_number"})
            url = f"{base_url}/pulls/{pr_number}/reviews"
            payload: dict[str, Any] = {"event": event}
            if body is not None:
                payload["body"] = body
            if comments:
                payload["comments"] = json.loads(comments)
            with httpx.Client(timeout=30) as client:
                resp = client.post(url, headers=_github_headers(token), json=payload)
            if resp.status_code not in (200, 201):
                return json.dumps({"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"})
            return json.dumps({"success": True, "review": resp.json()})
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})

    @tool
    def update_pr_review(review_id: int, body: str) -> str:
        """Update the body of an existing review on the current pull request.

        Args:
            review_id: The ID of the review to update.
            body: New review body text.

        Returns:
            JSON string with success status and updated review data.
        """
        try:
            if not token or not pr_number:
                return json.dumps({"success": False, "error": "Missing token or pr_number"})
            url = f"{base_url}/pulls/{pr_number}/reviews/{review_id}"
            with httpx.Client(timeout=30) as client:
                resp = client.put(url, headers=_github_headers(token), json={"body": body})
            if resp.status_code != 200:
                return json.dumps({"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"})
            return json.dumps({"success": True, "review": resp.json()})
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})

    @tool
    def dismiss_pr_review(review_id: int, message: str) -> str:
        """Dismiss a review on the current pull request.

        Args:
            review_id: The ID of the review to dismiss.
            message: Reason for dismissing the review.

        Returns:
            JSON string with success status and dismissed review data.
        """
        try:
            if not token or not pr_number:
                return json.dumps({"success": False, "error": "Missing token or pr_number"})
            url = f"{base_url}/pulls/{pr_number}/reviews/{review_id}/dismissals"
            with httpx.Client(timeout=30) as client:
                resp = client.put(url, headers=_github_headers(token), json={"message": message})
            if resp.status_code != 200:
                return json.dumps({"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"})
            return json.dumps({"success": True, "review": resp.json()})
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})

    @tool
    def submit_pr_review(review_id: int, event: str, body: str | None = None) -> str:
        """Submit a pending review on the current pull request.

        Use this when a review was created without an event (pending state).

        Args:
            review_id: The ID of the pending review to submit.
            event: Review action — one of APPROVE, REQUEST_CHANGES, or COMMENT.
            body: Optional body text for the review submission.

        Returns:
            JSON string with success status and submitted review data.
        """
        try:
            if not token or not pr_number:
                return json.dumps({"success": False, "error": "Missing token or pr_number"})
            url = f"{base_url}/pulls/{pr_number}/reviews/{review_id}/events"
            payload: dict[str, Any] = {"event": event}
            if body is not None:
                payload["body"] = body
            with httpx.Client(timeout=30) as client:
                resp = client.post(url, headers=_github_headers(token), json=payload)
            if resp.status_code not in (200, 201):
                return json.dumps({"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"})
            return json.dumps({"success": True, "review": resp.json()})
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})

    @tool
    def list_pr_review_comments(review_id: int | None = None) -> str:
        """List comments on a pull request review.

        Args:
            review_id: If provided, list comments for that specific review.
                If omitted, list all review comments on the PR.

        Returns:
            JSON string with success status and comments list.
        """
        try:
            if not token or not pr_number:
                return json.dumps({"success": False, "error": "Missing token or pr_number"})
            if review_id is not None:
                url = f"{base_url}/pulls/{pr_number}/reviews/{review_id}/comments"
            else:
                url = f"{base_url}/pulls/{pr_number}/comments"
            with httpx.Client(timeout=30) as client:
                resp = client.get(url, headers=_github_headers(token))
            if resp.status_code != 200:
                return json.dumps({"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"})
            return json.dumps({"success": True, "comments": resp.json()})
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})

    return [
        commit_and_open_pr,
        github_comment,
        list_pr_reviews,
        get_pr_review,
        create_pr_review,
        update_pr_review,
        dismiss_pr_review,
        submit_pr_review,
        list_pr_review_comments,
    ]
