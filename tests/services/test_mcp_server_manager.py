"""Tests for MCPServerManager service."""

import pytest
from pydantic import ValidationError

from cat.protocols.model_context.server import MCPServer
from cat.services.mcp_servers import MCPServerManager

# ── Model validation ────────────────────────────────────────────────


class TestMCPServer:
    """Unit tests for the MCPServer Pydantic model (from protocol layer)."""

    def test_valid_server(self):
        server = MCPServer(
            name="test-server",
            description="A test server",
            url="http://localhost:8000/mcp",
        )
        assert server.name == "test-server"
        assert server.description == "A test server"
        assert str(server.url) == "http://localhost:8000/mcp"
        assert server.auth_type == "none"  # default

    def test_auth_type_apikey(self):
        server = MCPServer(
            name="secured",
            description="Needs API key",
            url="http://localhost:8000/mcp",
            auth_type="apikey",
        )
        assert server.auth_type == "apikey"

    def test_invalid_url_rejected(self):
        with pytest.raises(ValidationError):
            MCPServer(name="bad", description="bad", url="not-a-url")

    def test_name_required(self):
        with pytest.raises(ValidationError):
            MCPServer(description="no name", url="http://localhost:8000/mcp")

    def test_description_required(self):
        with pytest.raises(ValidationError):
            MCPServer(name="no-desc", url="http://localhost:8000/mcp")

    def test_url_required(self):
        with pytest.raises(ValidationError):
            MCPServer(name="no-url", description="no url")

    def test_invalid_auth_type_rejected(self):
        with pytest.raises(ValidationError):
            MCPServer(
                name="bad",
                description="bad auth",
                url="http://localhost:8000/mcp",
                auth_type="digest",
            )


class TestMCPServerManagerSettings:
    """Unit tests for the nested Settings model."""

    def test_defaults_empty(self):
        settings = MCPServerManager.Settings()
        assert settings.servers == []

    def test_with_servers(self):
        settings = MCPServerManager.Settings(
            servers=[
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
        )
        assert len(settings.servers) == 2
        assert settings.servers[0].name == "server-a"
        assert settings.servers[1].name == "server-b"

    def test_from_dict(self):
        payload = {
            "servers": [
                {
                    "name": "my-server",
                    "description": "My server",
                    "url": "http://localhost:8000/mcp",
                },
            ]
        }
        settings = MCPServerManager.Settings.model_validate(payload)
        assert len(settings.servers) == 1
        assert settings.servers[0].name == "my-server"
        assert settings.servers[0].description == "My server"

    def test_from_dict_with_auth_type(self):
        payload = {
            "servers": [
                {
                    "name": "secured-server",
                    "description": "Secured",
                    "url": "http://localhost:8000/mcp",
                    "auth_type": "apikey",
                },
            ]
        }
        settings = MCPServerManager.Settings.model_validate(payload)
        assert settings.servers[0].auth_type == "apikey"

    def test_invalid_server_in_list_rejected(self):
        with pytest.raises(ValidationError):
            MCPServerManager.Settings(
                servers=[{"name": "bad", "description": "bad", "url": "not-a-url"}]
            )

    def test_missing_description_rejected(self):
        with pytest.raises(ValidationError):
            MCPServerManager.Settings(
                servers=[{"name": "no-desc", "url": "http://localhost:8000/mcp"}]
            )


class TestMCPServerManagerMetadata:
    """Unit tests for service class attributes."""

    def test_service_type(self):
        assert MCPServerManager.service_type == "mcp_servers"

    def test_slug(self):
        assert MCPServerManager.slug == "system"

    def test_lifecycle(self):
        from cat.services.service import SingletonService

        assert issubclass(MCPServerManager, SingletonService)
        assert MCPServerManager.lifecycle == "singleton"

    def test_has_settings_class(self):
        assert hasattr(MCPServerManager, "Settings")
        from pydantic import BaseModel

        assert issubclass(MCPServerManager.Settings, BaseModel)

    def test_settings_db_key_default(self):
        """DB key before registration (plugin_id is None)."""
        original = MCPServerManager.plugin_id
        try:
            MCPServerManager.plugin_id = None
            key = MCPServerManager._settings_db_key()
            assert key == "settings_None_mcp_servers_system"
        finally:
            MCPServerManager.plugin_id = original

    def test_settings_db_key_after_registration(self):
        """DB key after factory sets plugin_id to 'core'."""
        original = MCPServerManager.plugin_id
        try:
            MCPServerManager.plugin_id = "core"
            key = MCPServerManager._settings_db_key()
            assert key == "settings_core_mcp_servers_system"
        finally:
            MCPServerManager.plugin_id = original


# ── Integration with the running app ────────────────────────────────


class TestMCPServerManagerIntegration:
    """Tests that require the full FastAPI app bootstrapped."""

    def test_registered_in_factory(self, client):
        """MCPServerManager appears in the factory class_index."""
        ccat = client.app.state.ccat
        assert "mcp_servers" in ccat.factory.class_index
        assert "system" in ccat.factory.class_index["mcp_servers"]
        assert ccat.factory.class_index["mcp_servers"]["system"] is MCPServerManager

    def test_appears_in_settings_list(self, client, admin_headers, api_prefix):
        """GET /settings includes the MCP Servers entry."""
        response = client.get(f"{api_prefix}/settings", headers=admin_headers)
        assert response.status_code == 200

        entries = response.json()
        mcp_entry = next(
            (e for e in entries if e["type"] == "mcp_servers"),
            None,
        )
        assert mcp_entry is not None
        assert mcp_entry["slug"] == "system"
        assert mcp_entry["name"] == "MCP Servers"
        assert mcp_entry["plugin_id"] == "core"
        assert "schema" in mcp_entry
        assert mcp_entry["schema"] is not None

    def test_default_value_empty_servers(self, client, admin_headers, api_prefix):
        """Default settings have a servers list (empty on first run)."""
        response = client.get(f"{api_prefix}/settings", headers=admin_headers)
        entries = response.json()
        mcp_entry = next(e for e in entries if e["type"] == "mcp_servers")
        assert isinstance(mcp_entry["value"]["servers"], list)

    def test_put_settings_adds_server(self, client, admin_headers, api_prefix):
        """PUT /settings/{id} adds an MCP server and it persists."""
        settings_id = "core__mcp_servers__system"
        payload = {
            "servers": [
                {
                    "name": "test-mcp",
                    "description": "Test MCP server",
                    "url": "http://test-mcp:8000/mcp",
                },
            ]
        }
        response = client.put(
            f"{api_prefix}/settings/{settings_id}", json=payload, headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert data["value"]["servers"][0]["name"] == "test-mcp"

        # Verify persistence via GET
        response = client.get(f"{api_prefix}/settings", headers=admin_headers)
        entries = response.json()
        mcp_entry = next(e for e in entries if e["type"] == "mcp_servers")
        assert len(mcp_entry["value"]["servers"]) == 1
        assert mcp_entry["value"]["servers"][0]["name"] == "test-mcp"
