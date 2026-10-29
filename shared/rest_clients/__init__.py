# Vendored, trimmed REST clients for standalone TSG — Jira & Chalk only.
# The parent monorepo's rest_clients/__init__ eagerly imports NBOP/SM360/Genesis/Century/
# Apollo/SharePoint/keepwarm (plus an 8 MB nbop_api_cache.db), none of which TSG uses.
# Omitting them keeps this package self-contained and small. JiraRestClient / ChalkRestClient
# are the MCP-aware wrappers from .mcp_clients, matching the parent __init__'s re-exports.
from .session_manager import RESTSessionManager  # noqa: F401
from .mcp_clients import (  # noqa: F401
    JiraRestClient, ChalkRestClient, McpJiraClient, McpChalkClient,
)
