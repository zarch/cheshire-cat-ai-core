import json
from typing import List

from pydantic import BaseModel

from cat import log
from cat.env import get_env
from cat.protocols.model_context.server import MCPServer
from cat.services.service import SingletonService


class MCPServerManager(SingletonService):
    """System-wide MCP server configuration.

    Stores the list of MCP servers available to all users.
    Uses MCPServer from the protocol layer for consistency with
    client auth resolution (MCPServer.to_fastmcp_auth).

    Exposed via /api/v2/settings for admin configuration.
    On first boot, seeds from CCAT_MCP_SERVERS env var (JSON array).
    """

    service_type = "mcp_servers"
    slug = "system"
    name = "MCP Servers"
    description = "MCP servers available to all users by default."

    class Settings(BaseModel):
        servers: List[MCPServer] = []

    async def setup(self) -> None:
        """Seed MCP servers from CCAT_MCP_SERVERS env var on first boot.

        Reads a JSON array of server configs from the environment.
        Only seeds if:
          - the env var is set and non-empty
          - the parsed JSON is a non-empty array
          - the DB has no settings yet (DB wins over env var)
        """

        env_servers = get_env("CCAT_MCP_SERVERS")
        if not env_servers:
            return

        # Check if DB already has settings (DB wins over env var)
        from cat.db import DB

        try:
            raw = await DB.load(self._settings_db_key())
        except Exception as e:
            log.error(f"Failed to read DB during MCP seed: {e}")
            return

        if raw is not None:
            log.info("MCP servers already configured, skipping env var seed.")
            return

        # Parse and validate env var
        try:
            servers_data = json.loads(env_servers)
            if not isinstance(servers_data, list):
                raise ValueError("Expected a JSON array")
            if len(servers_data) == 0:
                log.info("CCAT_MCP_SERVERS is empty, skipping seed.")
                return

            settings = self.Settings(servers=servers_data)
            await DB.save(self._settings_db_key(), settings.model_dump())
            log.info(
                f"Seeded {len(settings.servers)} MCP server(s) from CCAT_MCP_SERVERS."
            )
        except Exception as e:
            log.error(f"Failed to parse CCAT_MCP_SERVERS: {e}")
