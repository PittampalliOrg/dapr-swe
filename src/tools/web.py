"""Web / HTTP tools for dapr-swe agents.

These tools make outbound HTTP requests and do not require the sandbox.
"""

from __future__ import annotations

import httpx
from dapr_agents import tool
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Tool: fetch_url
# ---------------------------------------------------------------------------
class FetchUrlArgs(BaseModel):
    url: str = Field(description="URL to fetch")


@tool(args_model=FetchUrlArgs)
def fetch_url(url: str) -> str:
    """Fetch a URL and return its content as plain text (HTML tags stripped to markdown-ish text)."""
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    text = resp.text

    # Simple HTML-to-text conversion: strip tags, decode entities
    if "html" in content_type:
        text = _strip_html(text)

    # Truncate very large pages to avoid blowing context
    max_chars = 50_000
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n... [truncated]"
    return text


# ---------------------------------------------------------------------------
# Tool: http_request
# ---------------------------------------------------------------------------
class HttpRequestArgs(BaseModel):
    url: str = Field(description="Request URL")
    method: str = Field(default="GET", description="HTTP method (GET, POST, PUT, DELETE, PATCH)")
    headers: dict | None = Field(default=None, description="Optional request headers")
    data: str | None = Field(default=None, description="Optional request body")


@tool(args_model=HttpRequestArgs)
def http_request(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    data: str | None = None,
) -> str:
    """Make a generic HTTP request and return the response body."""
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.request(
            method=method.upper(),
            url=url,
            headers=headers or {},
            content=data,
        )

    status_line = f"HTTP {resp.status_code}"
    body = resp.text

    # Truncate large responses
    max_chars = 50_000
    if len(body) > max_chars:
        body = body[:max_chars] + "\n\n... [truncated]"

    return f"{status_line}\n{body}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _strip_html(html: str) -> str:
    """Minimal HTML to text conversion without external dependencies."""
    import re

    # Remove script and style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Replace block-level tags with newlines
    text = re.sub(r"<(br|p|div|h[1-6]|li|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common HTML entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Convenience list of all web tools
# ---------------------------------------------------------------------------
web_tools = [fetch_url, http_request]
