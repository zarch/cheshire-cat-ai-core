from typing import List

from pydantic import BaseModel

from cat.protocols.model_context.server import MCPServer
from cat.services.service import SingletonService


class MCPServerManager(SingletonService):
    """System-wide MCP server configuration.

    Stores the list of MCP servers available to all users.
    Uses MCPServer from the protocol layer for consistency with
    client auth resolution (MCPServer.to_fastmcp_auth).

    Exposed via /api/v2/settings for admin configuration.
    """

    service_type = "mcp_servers"
    slug = "system"
    name = "MCP Servers"
    description = "MCP servers available to all users by default."

    class Settings(BaseModel):
        servers: List[MCPServer] = []
