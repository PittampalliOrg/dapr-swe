"""dapr-swe agent tools.

Each module exposes a factory function that returns ``@tool``-decorated
callables bound to the provided context (sandbox, issue_context, etc.)::

    from src.tools.sandbox import make_sandbox_tools
    from src.tools.github import make_github_tools
    from src.tools.web import make_web_tools
    from src.tools.linear import make_linear_tools
    from src.tools.slack import make_slack_tools
"""

from src.tools.sandbox import make_sandbox_tools, make_readonly_sandbox_tools, make_test_tools
from src.tools.github import make_github_tools
from src.tools.web import make_web_tools
from src.tools.linear import make_linear_tools
from src.tools.slack import make_slack_tools

__all__ = [
    "make_sandbox_tools",
    "make_readonly_sandbox_tools",
    "make_test_tools",
    "make_github_tools",
    "make_web_tools",
    "make_linear_tools",
    "make_slack_tools",
]
