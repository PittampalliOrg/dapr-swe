"""Web / HTTP tools for dapr-swe agents.

Provides ``make_web_tools()`` which returns ``@tool``-decorated functions
for web search, HTTP requests, and URL fetching.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
from dapr_agents.tool import tool

logger = logging.getLogger(__name__)


def _is_url_safe(url: str) -> tuple[bool, str]:
    """Check if a URL is safe to request (not targeting private/internal networks)."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False, "Could not parse hostname from URL"

        try:
            addr_infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return False, f"Could not resolve hostname: {hostname}"

        for addr_info in addr_infos:
            ip_str = addr_info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False, f"URL resolves to blocked address: {ip_str}"

        return True, ""
    except Exception as exc:
        return False, f"URL validation error: {exc}"


def _strip_html(html: str) -> str:
    """Minimal HTML to text conversion without heavy dependencies."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<(br|p|div|h[1-6]|li|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in [
        ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " "),
    ]:
        text = text.replace(entity, char)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def make_web_tools() -> list:
    """Create web tool functions.

    Returns:
        List of ``@tool``-decorated callables.
    """

    @tool
    def web_search(query: str, num_results: int = 5) -> str:
        """Search the web using Exa to find relevant information.

        Use this when you need documentation, code examples, GitHub repos,
        news, or research papers to help complete a task.

        Args:
            query: The search query.
            num_results: Number of results to return (default 5).

        Returns:
            JSON string with success, results, and error fields.
        """
        api_key = os.environ.get("EXA_API_KEY")
        if not api_key:
            return json.dumps({
                "success": False,
                "error": "EXA_API_KEY is not configured. Add it to your environment variables.",
            })

        try:
            from exa_py import Exa

            client = Exa(api_key=api_key)
            result = client.search_and_contents(
                query,
                text=True,
                num_results=num_results,
                type="auto",
            )
            return json.dumps({"success": True, "results": str(result), "error": None})
        except Exception as exc:
            logger.exception("web_search failed")
            return json.dumps({"success": False, "results": None, "error": f"{type(exc).__name__}: {exc}"})

    @tool
    def http_request(
        url: str,
        method: str = "GET",
        headers: str | None = None,
        data: str | None = None,
    ) -> str:
        """Make an HTTP request to an API or web service.

        Requests to private/internal IP addresses are blocked for security.

        Args:
            url: Target URL.
            method: HTTP method (GET, POST, PUT, DELETE, PATCH).
            headers: Optional JSON-encoded dict of request headers.
            data: Optional request body (string or JSON-encoded dict).

        Returns:
            JSON string with success, status_code, headers, content, and url fields.
        """
        is_safe, reason = _is_url_safe(url)
        if not is_safe:
            return json.dumps({
                "success": False,
                "status_code": 0,
                "content": f"Request blocked: {reason}",
                "url": url,
            })

        try:
            parsed_headers: dict[str, str] = {}
            if headers:
                parsed_headers = json.loads(headers)

            kwargs: dict[str, Any] = {"headers": parsed_headers}
            if data:
                # Try to parse as JSON dict, fall back to raw string
                try:
                    kwargs["json"] = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    kwargs["content"] = data

            with httpx.Client(timeout=30, follow_redirects=True) as client:
                resp = client.request(method.upper(), url, **kwargs)

            try:
                content = resp.json()
            except Exception:
                content = resp.text

            return json.dumps({
                "success": resp.status_code < 400,
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "content": content,
                "url": str(resp.url),
            }, default=str)
        except httpx.TimeoutException:
            return json.dumps({
                "success": False,
                "status_code": 0,
                "content": f"Request timed out",
                "url": url,
            })
        except Exception as exc:
            return json.dumps({
                "success": False,
                "status_code": 0,
                "content": f"Request error: {exc}",
                "url": url,
            })

    @tool
    def fetch_url(url: str) -> str:
        """Fetch a URL and return its content converted to readable text.

        HTML is converted to clean plain text. Very large pages are truncated.

        After receiving the content, synthesize it into a helpful response;
        do not show raw text to the user unless requested.

        Args:
            url: The URL to fetch (must be HTTP or HTTPS).

        Returns:
            The page content as plain text, or an error message.
        """
        try:
            with httpx.Client(
                timeout=30,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; DaprSWE/1.0)"},
            ) as client:
                resp = client.get(url)
                resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            text = resp.text

            if "html" in content_type:
                text = _strip_html(text)

            max_chars = 50_000
            if len(text) > max_chars:
                text = text[:max_chars] + "\n\n... [truncated]"
            return text
        except Exception as exc:
            return f"Error fetching {url}: {exc}"

    return [web_search, http_request, fetch_url]
