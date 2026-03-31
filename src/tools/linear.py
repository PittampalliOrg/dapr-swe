"""Linear project management tools for dapr-swe agents.

Provides ``make_linear_tools(issue_context)`` which returns ``@tool``-decorated
functions for interacting with the Linear API (issues, comments, teams).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from dapr_agents.tool import tool

logger = logging.getLogger(__name__)

LINEAR_API_URL = "https://api.linear.app/graphql"


def _linear_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }


def _gql(api_key: str, query: str, variables: dict | None = None) -> dict[str, Any]:
    """Execute a GraphQL query against the Linear API."""
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables
    with httpx.Client(timeout=30) as client:
        resp = client.post(LINEAR_API_URL, headers=_linear_headers(api_key), json=payload)
    if resp.status_code != 200:
        return {"errors": [{"message": f"HTTP {resp.status_code}: {resp.text}"}]}
    return resp.json()


def make_linear_tools(issue_context: dict[str, Any]) -> list:
    """Create Linear tool functions bound to *issue_context*.

    ``issue_context`` may contain:
        - ``linear_issue_id`` – the UUID of the current Linear issue
        - ``linear_team_id`` – the default team ID

    The Linear API key is read from ``LINEAR_API_KEY`` env var.

    Args:
        issue_context: Dict with Linear metadata.

    Returns:
        List of ``@tool``-decorated callables.
    """
    default_issue_id = issue_context.get("linear_issue_id", "")

    def _get_key() -> str | None:
        return os.environ.get("LINEAR_API_KEY")

    # ------------------------------------------------------------------
    # linear_get_issue
    # ------------------------------------------------------------------
    @tool
    def linear_get_issue(issue_id: str = "") -> str:
        """Get a Linear issue by its ID.

        Args:
            issue_id: The Linear issue UUID. Defaults to the current issue.

        Returns:
            JSON string with issue details or error.
        """
        api_key = _get_key()
        if not api_key:
            return json.dumps({"success": False, "error": "LINEAR_API_KEY not set"})
        target = issue_id or default_issue_id
        if not target:
            return json.dumps({"success": False, "error": "No issue_id provided"})

        query = """
        query($id: String!) {
            issue(id: $id) {
                id identifier title description state { name }
                priority assignee { name email }
                labels { nodes { name } }
                createdAt updatedAt
            }
        }
        """
        try:
            result = _gql(api_key, query, {"id": target})
            if "errors" in result:
                return json.dumps({"success": False, "error": str(result["errors"])})
            return json.dumps({"success": True, "issue": result.get("data", {}).get("issue")})
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})

    # ------------------------------------------------------------------
    # linear_update_issue
    # ------------------------------------------------------------------
    @tool
    def linear_update_issue(
        issue_id: str = "",
        title: str | None = None,
        description: str | None = None,
        assignee_id: str | None = None,
        priority: int | None = None,
        state_id: str | None = None,
    ) -> str:
        """Update an existing Linear issue.

        Args:
            issue_id: Linear issue UUID (defaults to current issue).
            title: New title.
            description: New markdown description.
            assignee_id: User ID to assign.
            priority: Priority (0=none, 1=urgent, 2=high, 3=medium, 4=low).
            state_id: Workflow state ID to transition to.

        Returns:
            JSON string with success status.
        """
        api_key = _get_key()
        if not api_key:
            return json.dumps({"success": False, "error": "LINEAR_API_KEY not set"})
        target = issue_id or default_issue_id
        if not target:
            return json.dumps({"success": False, "error": "No issue_id provided"})

        input_fields: dict[str, Any] = {}
        if title is not None:
            input_fields["title"] = title
        if description is not None:
            input_fields["description"] = description
        if assignee_id is not None:
            input_fields["assigneeId"] = assignee_id
        if priority is not None:
            input_fields["priority"] = priority
        if state_id is not None:
            input_fields["stateId"] = state_id

        if not input_fields:
            return json.dumps({"success": False, "error": "No fields to update"})

        query = """
        mutation($id: String!, $input: IssueUpdateInput!) {
            issueUpdate(id: $id, input: $input) {
                success
                issue { id identifier title state { name } }
            }
        }
        """
        try:
            result = _gql(api_key, query, {"id": target, "input": input_fields})
            if "errors" in result:
                return json.dumps({"success": False, "error": str(result["errors"])})
            data = result.get("data", {}).get("issueUpdate", {})
            return json.dumps({"success": data.get("success", False), "issue": data.get("issue")})
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})

    # ------------------------------------------------------------------
    # linear_comment
    # ------------------------------------------------------------------
    @tool
    def linear_comment(comment_body: str, issue_id: str = "") -> str:
        """Post a comment to a Linear issue.

        Use this to communicate progress, completion, or updates to stakeholders.
        For example, after opening a PR: 'Completed implementation and opened PR: <url>'

        Args:
            comment_body: Markdown-formatted comment text.
            issue_id: Linear issue UUID (defaults to current issue).

        Returns:
            JSON string with success status.
        """
        api_key = _get_key()
        if not api_key:
            return json.dumps({"success": False, "error": "LINEAR_API_KEY not set"})
        target = issue_id or default_issue_id
        if not target:
            return json.dumps({"success": False, "error": "No issue_id provided"})

        query = """
        mutation($input: CommentCreateInput!) {
            commentCreate(input: $input) {
                success
                comment { id body createdAt }
            }
        }
        """
        try:
            result = _gql(api_key, query, {"input": {"issueId": target, "body": comment_body}})
            if "errors" in result:
                return json.dumps({"success": False, "error": str(result["errors"])})
            data = result.get("data", {}).get("commentCreate", {})
            return json.dumps({"success": data.get("success", False)})
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})

    # ------------------------------------------------------------------
    # linear_get_issue_comments
    # ------------------------------------------------------------------
    @tool
    def linear_get_issue_comments(issue_id: str = "") -> str:
        """Get all comments on a Linear issue.

        Args:
            issue_id: Linear issue UUID (defaults to current issue).

        Returns:
            JSON string with comments list.
        """
        api_key = _get_key()
        if not api_key:
            return json.dumps({"success": False, "error": "LINEAR_API_KEY not set"})
        target = issue_id or default_issue_id
        if not target:
            return json.dumps({"success": False, "error": "No issue_id provided"})

        query = """
        query($id: String!) {
            issue(id: $id) {
                comments { nodes { id body createdAt user { name email } } }
            }
        }
        """
        try:
            result = _gql(api_key, query, {"id": target})
            if "errors" in result:
                return json.dumps({"success": False, "error": str(result["errors"])})
            comments = (
                result.get("data", {}).get("issue", {}).get("comments", {}).get("nodes", [])
            )
            return json.dumps({"success": True, "comments": comments})
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})

    # ------------------------------------------------------------------
    # linear_list_teams
    # ------------------------------------------------------------------
    @tool
    def linear_list_teams() -> str:
        """List all teams in the Linear workspace.

        Returns:
            JSON string with teams list (id, name, key, description).
        """
        api_key = _get_key()
        if not api_key:
            return json.dumps({"success": False, "error": "LINEAR_API_KEY not set"})

        query = """
        query {
            teams { nodes { id name key description } }
        }
        """
        try:
            result = _gql(api_key, query)
            if "errors" in result:
                return json.dumps({"success": False, "error": str(result["errors"])})
            teams = result.get("data", {}).get("teams", {}).get("nodes", [])
            return json.dumps({"success": True, "teams": teams})
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})

    # ------------------------------------------------------------------
    # linear_create_issue
    # ------------------------------------------------------------------
    @tool
    def linear_create_issue(
        team_id: str,
        title: str,
        description: str | None = None,
        assignee_id: str | None = None,
        priority: int | None = None,
        state_id: str | None = None,
        project_id: str | None = None,
    ) -> str:
        """Create a new Linear issue.

        Args:
            team_id: The ID of the team to create the issue in.
            title: Issue title.
            description: Optional markdown description.
            assignee_id: Optional user ID to assign.
            priority: Optional priority (0=none, 1=urgent, 2=high, 3=medium, 4=low).
            state_id: Optional workflow state ID.
            project_id: Optional project ID.

        Returns:
            JSON string with success status and created issue details.
        """
        api_key = _get_key()
        if not api_key:
            return json.dumps({"success": False, "error": "LINEAR_API_KEY not set"})

        input_fields: dict[str, Any] = {"teamId": team_id, "title": title}
        if description is not None:
            input_fields["description"] = description
        if assignee_id is not None:
            input_fields["assigneeId"] = assignee_id
        if priority is not None:
            input_fields["priority"] = priority
        if state_id is not None:
            input_fields["stateId"] = state_id
        if project_id is not None:
            input_fields["projectId"] = project_id

        query = """
        mutation($input: IssueCreateInput!) {
            issueCreate(input: $input) {
                success
                issue { id identifier title url state { name } }
            }
        }
        """
        try:
            result = _gql(api_key, query, {"input": input_fields})
            if "errors" in result:
                return json.dumps({"success": False, "error": str(result["errors"])})
            data = result.get("data", {}).get("issueCreate", {})
            return json.dumps({"success": data.get("success", False), "issue": data.get("issue")})
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})

    # ------------------------------------------------------------------
    # linear_delete_issue
    # ------------------------------------------------------------------
    @tool
    def linear_delete_issue(issue_id: str) -> str:
        """Delete a Linear issue.

        Args:
            issue_id: The Linear issue UUID to delete.

        Returns:
            JSON string with success status.
        """
        api_key = _get_key()
        if not api_key:
            return json.dumps({"success": False, "error": "LINEAR_API_KEY not set"})

        query = """
        mutation($id: String!) {
            issueDelete(id: $id) { success }
        }
        """
        try:
            result = _gql(api_key, query, {"id": issue_id})
            if "errors" in result:
                return json.dumps({"success": False, "error": str(result["errors"])})
            data = result.get("data", {}).get("issueDelete", {})
            return json.dumps({"success": data.get("success", False)})
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})

    return [
        linear_get_issue,
        linear_update_issue,
        linear_comment,
        linear_get_issue_comments,
        linear_list_teams,
        linear_create_issue,
        linear_delete_issue,
    ]
