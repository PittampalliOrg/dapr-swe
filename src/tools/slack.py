"""Slack tools for dapr-swe agents.

Provides ``make_slack_tools(issue_context)`` which returns a ``@tool``-decorated
function for posting replies to a Slack thread.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx
from dapr_agents.tool import tool

logger = logging.getLogger(__name__)


def _convert_mentions_to_slack_format(text: str) -> str:
    """Convert @Name(USER_ID) mentions to Slack's <@USER_ID> format."""
    return re.sub(r"@\w+\(([A-Z0-9]+)\)", r"<@\1>", text)


def make_slack_tools(issue_context: dict[str, Any]) -> list:
    """Create Slack tool functions bound to *issue_context*.

    ``issue_context`` should contain:
        - ``slack_channel_id`` – Slack channel ID
        - ``slack_thread_ts`` – Thread timestamp to reply to

    The Slack bot token is read from ``SLACK_BOT_TOKEN`` env var.

    Args:
        issue_context: Dict with Slack thread metadata.

    Returns:
        List of ``@tool``-decorated callables.
    """
    channel_id = issue_context.get("slack_channel_id", "")
    thread_ts = issue_context.get("slack_thread_ts", "")

    @tool
    def slack_thread_reply(message: str) -> str:
        """Post a message to the current Slack thread.

        Format messages using Slack's mrkdwn format, NOT standard Markdown.
        Key differences: *bold*, _italic_, ~strikethrough~, <url|link text>,
        bullet lists with bullet character, triple-backtick code blocks, > blockquotes.
        Do NOT use **bold**, [link](url), or other standard Markdown syntax.

        To mention a user, use Slack's format: <@USER_ID>.

        Args:
            message: The message to post (Slack mrkdwn format).

        Returns:
            JSON string with success status and error if any.
        """
        try:
            if not channel_id or not thread_ts:
                return json.dumps({
                    "success": False,
                    "error": "Missing slack_channel_id or slack_thread_ts in issue_context",
                })
            if not message.strip():
                return json.dumps({"success": False, "error": "Message cannot be empty"})

            slack_token = os.environ.get("SLACK_BOT_TOKEN")
            if not slack_token:
                return json.dumps({"success": False, "error": "SLACK_BOT_TOKEN not set"})

            formatted = _convert_mentions_to_slack_format(message)

            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={
                        "Authorization": f"Bearer {slack_token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "channel": channel_id,
                        "thread_ts": thread_ts,
                        "text": formatted,
                    },
                )

            data = resp.json()
            if data.get("ok"):
                return json.dumps({"success": True})
            return json.dumps({"success": False, "error": data.get("error", "Unknown Slack error")})
        except Exception as exc:
            return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})

    return [slack_thread_reply]
