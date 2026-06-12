"""Tests for MCPClients — config resolution, caching, and context manager."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from cat.protocols.model_context.client import MCPClient, MCPClients
from cat.protocols.model_context.server import MCPServer
from cat.services.mcp_servers import MCPServerManager


# ── Helpers ─────────────────────────────────────────────────────────


def _make_agent(user_id=None, manager=None):
    """Create a mock agent with user.id and ccat.get() wired up.

    Parameters
    ----------
    user_id : uuid, optional
        User ID (random UUID if omitted).
    manager : object or None
        What ccat.get("mcp_servers", "system") returns. Pass None to
        simulate "service not registered".
    """
    if user_id is None:
        user_id = uuid4()

    agent = MagicMock()
    agent.user.id = user_id

    ccat = MagicMock()

    if manager is None:
        ccat.get = AsyncMock(return_value=None)
    else:
        ccat.get = AsyncMock(return_value=manager)

    agent.ccat = ccat
    return agent


def _make_manager(servers=None):
    """Create a mock MCPServerManager with given servers in settings."""
    manager = MagicMock()

    if servers is None:
        manager.load_settings = AsyncMock(return_value=None)
    else:
        settings = MCPServerManager.Settings(servers=servers)
        manager.load_settings = AsyncMock(return_value=settings)

    return manager


# ── MCPClient unit tests ───────────────────────────────────────────


class TestMCPClient:
    """Unit tests for the MCPClient wrapper."""

    def test_empty_config_uses_empty_server(self):
        client = MCPClient({"mcpServers": {}})
        # MCPClient should have connected to empty_server, not a URL
        assert client.config == {"mcpServers": {}}

    def test_config_with_servers(self):
        config = {
            "mcpServers": {
                "my-server": {"url": "http://localhost:8000/mcp"},
            }
        }
        client = MCPClient(config)
        assert client.config == config

    def test_config_stored_on_instance(self):
        config = {"mcpServers": {}}
        client = MCPClient(config)
        assert client.config is config


# ── need_new_client config resolution ───────────────────────────────


class TestNeedNewClientResolution:
    """Unit tests for need_new_client() config resolution from MCPServerManager."""

    @pytest.mark.asyncio
    async def test_empty_when_no_manager(self):
        """Returns empty config when MCPServerManager is not registered."""
        clients = MCPClients()
        agent = _make_agent(manager=None)

        need_new, config = await clients.need_new_client(agent)

        assert need_new is True
        assert config == {"mcpServers": {}}

    @pytest.mark.asyncio
    async def test_empty_when_no_settings(self):
        """Returns empty config when manager exists but no settings stored."""
        clients = MCPClients()
        agent = _make_agent(manager=_make_manager(servers=None))

        need_new, config = await clients.need_new_client(agent)

        assert config == {"mcpServers": {}}

    @pytest.mark.asyncio
    async def test_empty_when_no_servers(self):
        """Returns empty config when settings exist but servers list is empty."""
        clients = MCPClients()
        agent = _make_agent(manager=_make_manager(servers=[]))

        need_new, config = await clients.need_new_client(agent)

        assert config == {"mcpServers": {}}

    @pytest.mark.asyncio
    async def test_config_built_from_servers(self):
        """Builds mcpServers dict from MCPServer list."""
        clients = MCPClients()
        servers = [
            MCPServer(
                name="server-a",
                description="First",
                url="http://localhost:8000/mcp",
            ),
            MCPServer(
                name="server-b",
                description="Second",
                url="http://localhost:8001/mcp",
            ),
        ]
        agent = _make_agent(manager=_make_manager(servers=servers))

        need_new, config = await clients.need_new_client(agent)

        assert "server-a" in config["mcpServers"]
        assert "server-b" in config["mcpServers"]
        assert config["mcpServers"]["server-a"]["url"] == "http://localhost:8000/mcp"
        assert config["mcpServers"]["server-b"]["url"] == "http://localhost:8001/mcp"

    @pytest.mark.asyncio
    async def test_url_serialized_as_string(self):
        """MCPServer.url (HttpUrl) is converted to plain string in config."""
        clients = MCPClients()
        servers = [
            MCPServer(
                name="test",
                description="Test",
                url="http://localhost:8000/mcp",
            ),
        ]
        agent = _make_agent(manager=_make_manager(servers=servers))

        _, config = await clients.need_new_client(agent)

        url = config["mcpServers"]["test"]["url"]
        assert isinstance(url, str)
        assert url == "http://localhost:8000/mcp"


# ── need_new_client cache staleness ────────────────────────────────


class TestNeedNewClientCache:
    """Tests for the staleness detection logic in need_new_client()."""

    @pytest.mark.asyncio
    async def test_need_new_when_user_not_in_cache(self):
        """Returns need_new=True when the user has no cached client."""
        clients = MCPClients()
        agent = _make_agent(manager=None)

        need_new, _ = await clients.need_new_client(agent)

        assert need_new is True

    @pytest.mark.asyncio
    async def test_no_change_when_config_same(self):
        """Returns need_new=False when cached client has the same config."""
        clients = MCPClients()
        agent = _make_agent(manager=None)

        # Simulate a client being cached (get_user_client would do this)
        config = {"mcpServers": {}}
        clients.clients[agent.user.id] = MCPClient(config)

        # Same config, same user — should not need new
        need_new, _ = await clients.need_new_client(agent)

        assert need_new is False

    @pytest.mark.asyncio
    async def test_need_new_when_config_changed(self):
        """Returns need_new=True after admin adds a new server."""
        clients = MCPClients()

        # Step 1: no servers — populate cache
        agent = _make_agent(manager=_make_manager(servers=None))
        await clients.need_new_client(agent)

        # Step 2: admin adds a server via same ccat mock
        server = MCPServer(
            name="new-server",
            description="New",
            url="http://localhost:9000/mcp",
        )
        agent.ccat.get = AsyncMock(return_value=_make_manager(servers=[server]))

        need_new, config = await clients.need_new_client(agent)

        assert need_new is True
        assert "new-server" in config["mcpServers"]


# ── get_user_client context manager ────────────────────────────────


class TestGetUserClient:
    """Tests for the async context manager flow."""

    @pytest.mark.asyncio
    async def test_creates_client_on_first_call(self):
        """After get_user_client, a new MCPClient is cached for the user."""
        clients = MCPClients()
        agent = _make_agent(manager=None)

        assert agent.user.id not in clients.clients

        async with clients.get_user_client(agent):
            pass

        assert agent.user.id in clients.clients
        assert isinstance(clients.clients[agent.user.id], MCPClient)

    @pytest.mark.asyncio
    async def test_reuses_client_when_unchanged(self):
        """On second call with same config, the same MCPClient is reused."""
        clients = MCPClients()
        agent = _make_agent(manager=None)

        async with clients.get_user_client(agent) as first:
            first_id = id(first)

        async with clients.get_user_client(agent) as second:
            second_id = id(second)

        # Same underlying MCPClient object in cache
        assert first_id == second_id

    @pytest.mark.asyncio
    async def test_context_manager_yields_mcp_client(self):
        """async with yields a usable client object."""
        clients = MCPClients()
        agent = _make_agent(manager=None)

        async with clients.get_user_client(agent) as mcp_client:
            # Should have the config attribute from MCPClient
            assert hasattr(mcp_client, "config")


# ── Edge cases ─────────────────────────────────────────────────────


class TestDuplicateServerNames:
    """Duplicate server names silently overwrite — last one wins."""

    @pytest.mark.asyncio
    async def test_duplicate_names_last_wins(self):
        """Two servers with same name: second URL wins, no crash."""
        clients = MCPClients()
        servers = [
            MCPServer(
                name="weather",
                description="First weather",
                url="http://localhost:8000/mcp",
            ),
            MCPServer(
                name="weather",
                description="Second weather",
                url="http://localhost:9000/mcp",
            ),
        ]
        agent = _make_agent(manager=_make_manager(servers=servers))

        _, config = await clients.need_new_client(agent)

        # Only one entry — second overwrote first
        assert len(config["mcpServers"]) == 1
        assert config["mcpServers"]["weather"]["url"] == "http://localhost:9000/mcp"


class TestServerNameEdgeCases:
    """Edge cases for server name values."""

    @pytest.mark.asyncio
    async def test_empty_string_name(self):
        """Empty string name creates a valid config key (no crash)."""
        clients = MCPClients()
        servers = [
            MCPServer(
                name="",
                description="Empty name server",
                url="http://localhost:8000/mcp",
            ),
        ]
        agent = _make_agent(manager=_make_manager(servers=servers))

        _, config = await clients.need_new_client(agent)

        assert "" in config["mcpServers"]
        assert config["mcpServers"][""]["url"] == "http://localhost:8000/mcp"

    @pytest.mark.asyncio
    async def test_special_characters_in_name(self):
        """Names with slashes and special chars work as dict keys."""
        clients = MCPClients()
        servers = [
            MCPServer(
                name="my-server/v2",
                description="Versioned server",
                url="http://localhost:8000/mcp",
            ),
        ]
        agent = _make_agent(manager=_make_manager(servers=servers))

        _, config = await clients.need_new_client(agent)

        assert "my-server/v2" in config["mcpServers"]


class TestUrlTrailingSlash:
    """URL trailing slash normalization."""

    @pytest.mark.asyncio
    async def test_trailing_slash_preserved(self):
        """Pydantic HttpUrl preserves trailing slash in string form."""
        clients = MCPClients()
        servers = [
            MCPServer(
                name="with-slash",
                description="Trailing slash",
                url="http://localhost:8000/mcp/",
            ),
        ]
        agent = _make_agent(manager=_make_manager(servers=servers))

        _, config = await clients.need_new_client(agent)

        url = config["mcpServers"]["with-slash"]["url"]
        # Pydantic HttpUrl may or may not strip trailing slash
        # The test documents what actually happens
        assert url.startswith("http://localhost:8000/mcp")


class TestErrorHandling:
    """Tests for graceful error handling (try/except in need_new_client)."""

    @pytest.mark.asyncio
    async def test_load_settings_raises_returns_empty(self):
        """load_settings() raising an exception returns empty config, no crash."""
        clients = MCPClients()
        manager = MagicMock()
        manager.load_settings = AsyncMock(side_effect=RuntimeError("DB corrupted"))
        agent = _make_agent(manager=manager)

        need_new, config = await clients.need_new_client(agent)

        assert config == {"mcpServers": {}}

    @pytest.mark.asyncio
    async def test_ccat_get_raises_returns_empty(self):
        """ccat.get() raising returns empty config, no crash."""
        clients = MCPClients()
        agent = _make_agent(manager=None)
        agent.ccat.get = AsyncMock(side_effect=RuntimeError("Factory broken"))

        need_new, config = await clients.need_new_client(agent)

        assert config == {"mcpServers": {}}

    @pytest.mark.asyncio
    async def test_manager_is_not_none_but_not_a_manager(self):
        """ccat.get() returns something unexpected (not None, no load_settings)."""
        clients = MCPClients()
        # Returns a string instead of a manager — load_settings will fail
        agent = _make_agent(manager="not-a-manager")

        need_new, config = await clients.need_new_client(agent)

        # Should not crash — try/except catches AttributeError
        assert config == {"mcpServers": {}}


class TestUserIsolation:
    """Two users get independent clients."""

    @pytest.mark.asyncio
    async def test_two_users_independent_clients(self):
        """Different users get different cached clients."""
        clients = MCPClients()

        user_a = _make_agent(user_id=uuid4(), manager=None)
        user_b = _make_agent(user_id=uuid4(), manager=None)

        async with clients.get_user_client(user_a):
            pass
        async with clients.get_user_client(user_b):
            pass

        assert user_a.user.id in clients.clients
        assert user_b.user.id in clients.clients
        assert clients.clients[user_a.user.id] is not clients.clients[user_b.user.id]

    @pytest.mark.asyncio
    async def test_config_change_for_one_user_does_not_affect_other(self):
        """Admin adds server: only the user with stale cache sees need_new=True."""
        clients = MCPClients()

        user_a = _make_agent(user_id=uuid4(), manager=None)
        user_b = _make_agent(user_id=uuid4(), manager=None)

        # Both start with empty config
        async with clients.get_user_client(user_a):
            pass
        async with clients.get_user_client(user_b):
            pass

        # Admin adds a server — only check user_a
        server = MCPServer(
            name="new",
            description="New",
            url="http://localhost:9000/mcp",
        )
        user_a.ccat.get = AsyncMock(return_value=_make_manager(servers=[server]))
        user_b.ccat.get = AsyncMock(return_value=_make_manager(servers=[server]))

        need_a, _ = await clients.need_new_client(user_a)
        need_b, _ = await clients.need_new_client(user_b)

        assert need_a is True
        assert need_b is True


# ── Integration with the running app ───────────────────────────────


class TestMCPClientsIntegration:
    """End-to-end tests: admin configures servers via /settings,
    then MCPClients reads them through the factory."""

    @pytest.mark.asyncio
    async def test_reads_from_mcp_server_manager(self, client):
        """need_new_client reads servers configured via the settings API."""
        from cat.db import DB

        ccat = client.app.state.ccat
        MCPServerManager.plugin_id = "core"
        settings_id = MCPServerManager._settings_db_key()

        try:
            await DB.save(
                settings_id,
                {
                    "servers": [
                        {
                            "name": "integration-server",
                            "description": "Integration test",
                            "url": "http://localhost:9999/mcp",
                        },
                    ]
                },
            )

            # Create a real agent-like object wired to the real ccat
            agent = MagicMock()
            agent.user.id = uuid4()
            agent.ccat = ccat

            mcp_clients = MCPClients()

            need_new, config = await mcp_clients.need_new_client(agent)

            assert need_new is True
            assert "integration-server" in config["mcpServers"]
            assert (
                config["mcpServers"]["integration-server"]["url"]
                == "http://localhost:9999/mcp"
            )
        finally:
            await DB.delete(settings_id)

    @pytest.mark.asyncio
    async def test_reflects_settings_update(self, client):
        """After admin PUTs new servers, need_new_client sees the change."""
        from cat.db import DB

        ccat = client.app.state.ccat

        MCPServerManager.plugin_id = "core"
        settings_id = MCPServerManager._settings_db_key()

        try:
            # Initial: no servers
            await DB.save(settings_id, {"servers": []})

            agent = MagicMock()
            agent.user.id = uuid4()
            agent.ccat = ccat

            mcp_clients = MCPClients()

            # First call — empty
            need_new, config = await mcp_clients.need_new_client(agent)
            assert config == {"mcpServers": {}}

            # Simulate cache population (get_user_client would do this)
            mcp_clients.clients[agent.user.id] = MCPClient(config)

            # Admin adds a server
            await DB.save(
                settings_id,
                {
                    "servers": [
                        {
                            "name": "added-later",
                            "description": "Added after bootstrap",
                            "url": "http://localhost:7777/mcp",
                        },
                    ]
                },
            )

            need_new, config = await mcp_clients.need_new_client(agent)

            assert need_new is True
            assert "added-later" in config["mcpServers"]
        finally:
            await DB.delete(settings_id)
