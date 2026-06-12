from contextlib import asynccontextmanager

from cachetools import TTLCache
from cat import log
from fastmcp import FastMCP, Client


# necessary in case of empty client config
empty_server = FastMCP("EmptyServer")


class MCPClient(Client):
    """Cat MCP client is scoped by user_id and does not keep a live connection to servers.
    We use caches waiting for the protocol to become stateless.
    """

    def __init__(self, config):

        # TODO: get addresses / tokens / api keys from DB
        self.config = config
        if len(config["mcpServers"]) == 0:
            super().__init__(empty_server)
        else:
            super().__init__(config)


class MCPClients:
    """Keep a cache of user scoped MCP clients"""

    def __init__(self):
        self.clients = TTLCache(maxsize=1000, ttl=60 * 10)

    @asynccontextmanager
    async def get_user_client(self, agent):
        """Return an MCP client scoped to the user, resolving server config
        from MCPServerManager (Tier 1 system-wide servers).

        The async context manager opens and closes the client connection,
        preserving the existing call site in base.py unchanged.
        """

        need_new, config = await self.need_new_client(agent)
        if need_new:
            self.clients[agent.user.id] = MCPClient(config)

        client = self.clients[agent.user.id]
        async with client as mcp_client:
            yield mcp_client

    async def need_new_client(self, agent) -> tuple[bool, dict]:
        """Resolve MCP server config from MCPServerManager and detect
        if the cached client is stale."""

        config = {"mcpServers": {}}

        # Tier 1: system-wide MCP servers (admin-configured via MCPServerManager)
        try:
            mcp_manager = await agent.ccat.get(
                "mcp_servers", "system", raise_error=False
            )
            if mcp_manager:
                settings = await mcp_manager.load_settings()
                if settings:
                    for server in settings.servers:
                        config["mcpServers"][server.name] = {"url": str(server.url)}
        except Exception as e:
            log.error(f"Error loading MCP server settings: {e}")

        # TODOV2: Tier 2 — profile MCPs (per-role, see 07-profile-mcp-access.md)
        # TODOV2: Tier 3 — user preferences (enable/disable, see 07-profile-mcp-access.md)

        need_new = (agent.user.id not in self.clients) or self.clients[
            agent.user.id
        ].config != config

        return need_new, config
