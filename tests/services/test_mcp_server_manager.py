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


# ── Seed from CCAT_MCP_SERVERS env var ──────────────────────────────


class TestMCPServerManagerSeed:
    """Tests for the setup() seed mechanism (CCAT_MCP_SERVERS env var)."""

    @pytest.mark.asyncio
    async def test_seed_from_env_var(self, monkeypatch):
        """setup() seeds servers from CCAT_MCP_SERVERS when DB is empty."""
        manager = MCPServerManager()
        manager.plugin_id = "core"

        monkeypatch.setenv(
            "CCAT_MCP_SERVERS",
            '[{"name":"eudox-mcp","description":"Eudox","url":"http://eudox-mcp:8000/mcp"}]',
        )

        # Mock DB to simulate empty DB
        seeded_data = {}
        from cat.db import DB

        async def mock_load(key):
            return None  # empty DB

        async def mock_save(key, value):
            seeded_data[key] = value

        monkeypatch.setattr(DB, "load", mock_load)
        monkeypatch.setattr(DB, "save", mock_save)

        await manager.setup()

        db_key = manager._settings_db_key()
        assert db_key in seeded_data
        assert len(seeded_data[db_key]["servers"]) == 1
        assert seeded_data[db_key]["servers"][0]["name"] == "eudox-mcp"

    @pytest.mark.asyncio
    async def test_seed_skipped_when_db_has_data(self, monkeypatch):
        """setup() does NOT overwrite existing admin config."""
        manager = MCPServerManager()
        manager.plugin_id = "core"

        monkeypatch.setenv(
            "CCAT_MCP_SERVERS",
            '[{"name":"env-server","description":"From env","url":"http://env:8000/mcp"}]',
        )

        from cat.db import DB

        async def mock_load(key):
            return {
                "servers": [
                    {
                        "name": "admin-server",
                        "description": "Admin",
                        "url": "http://admin:8000/mcp",
                    }
                ]
            }

        saved = {}

        async def mock_save(key, value):
            saved[key] = value

        monkeypatch.setattr(DB, "load", mock_load)
        monkeypatch.setattr(DB, "save", mock_save)

        await manager.setup()

        # Should NOT have saved — admin config preserved
        assert manager._settings_db_key() not in saved

    @pytest.mark.asyncio
    async def test_seed_skipped_when_no_env_var(self, monkeypatch):
        """setup() is a no-op when CCAT_MCP_SERVERS is not set."""
        manager = MCPServerManager()
        manager.plugin_id = "core"

        monkeypatch.delenv("CCAT_MCP_SERVERS", raising=False)

        from cat.db import DB

        saved = {}

        async def mock_save(key, value):
            saved[key] = value

        monkeypatch.setattr(DB, "save", mock_save)

        await manager.setup()

        assert manager._settings_db_key() not in saved

    @pytest.mark.asyncio
    async def test_seed_invalid_json_logged_and_skipped(self, monkeypatch):
        """setup() logs error and does not crash on invalid JSON."""
        manager = MCPServerManager()
        manager.plugin_id = "core"

        monkeypatch.setenv("CCAT_MCP_SERVERS", "not-json-at-all")

        from cat.db import DB

        saved = {}

        async def mock_load(key):
            return None

        async def mock_save(key, value):
            saved[key] = value

        monkeypatch.setattr(DB, "load", mock_load)
        monkeypatch.setattr(DB, "save", mock_save)

        # Should not raise
        await manager.setup()

        assert manager._settings_db_key() not in saved

    @pytest.mark.asyncio
    async def test_seed_not_array_logged_and_skipped(self, monkeypatch):
        """setup() rejects a non-array JSON value."""
        manager = MCPServerManager()
        manager.plugin_id = "core"

        monkeypatch.setenv("CCAT_MCP_SERVERS", '{"name":"wrong"}')

        from cat.db import DB

        saved = {}

        async def mock_load(key):
            return None

        async def mock_save(key, value):
            saved[key] = value

        monkeypatch.setattr(DB, "load", mock_load)
        monkeypatch.setattr(DB, "save", mock_save)

        await manager.setup()

        assert manager._settings_db_key() not in saved

    @pytest.mark.asyncio
    async def test_seed_invalid_server_in_array_logged_and_skipped(self, monkeypatch):
        """setup() rejects an array with invalid server objects."""
        manager = MCPServerManager()
        manager.plugin_id = "core"

        monkeypatch.setenv(
            "CCAT_MCP_SERVERS",
            '[{"name":"ok","description":"OK","url":"http://ok:8000/mcp"},'
            '{"name":"bad","url":"not-a-url"}]',
        )

        from cat.db import DB

        saved = {}

        async def mock_load(key):
            return None

        async def mock_save(key, value):
            saved[key] = value

        monkeypatch.setattr(DB, "load", mock_load)
        monkeypatch.setattr(DB, "save", mock_save)

        await manager.setup()

        # Entire batch fails validation — nothing saved
        assert manager._settings_db_key() not in saved

    @pytest.mark.asyncio
    async def test_seed_multiple_servers(self, monkeypatch):
        """setup() seeds multiple servers from env var."""
        manager = MCPServerManager()
        manager.plugin_id = "core"

        monkeypatch.setenv(
            "CCAT_MCP_SERVERS",
            '[{"name":"s1","description":"Server 1","url":"http://s1:8000/mcp"},'
            '{"name":"s2","description":"Server 2","url":"http://s2:8001/mcp","auth_type":"apikey"}]',
        )

        from cat.db import DB

        seeded_data = {}

        async def mock_load(key):
            return None

        async def mock_save(key, value):
            seeded_data[key] = value

        monkeypatch.setattr(DB, "load", mock_load)
        monkeypatch.setattr(DB, "save", mock_save)

        await manager.setup()

        db_key = manager._settings_db_key()
        assert len(seeded_data[db_key]["servers"]) == 2
        assert seeded_data[db_key]["servers"][0]["name"] == "s1"
        assert seeded_data[db_key]["servers"][1]["auth_type"] == "apikey"

    @pytest.mark.asyncio
    async def test_seed_empty_array_skipped(self, monkeypatch):
        """setup() skips CCAT_MCP_SERVERS=[] — nothing to seed."""
        manager = MCPServerManager()
        manager.plugin_id = "core"

        monkeypatch.setenv("CCAT_MCP_SERVERS", "[]")

        from cat.db import DB

        saved = {}

        async def mock_load(key):
            return None

        async def mock_save(key, value):
            saved[key] = value

        monkeypatch.setattr(DB, "load", mock_load)
        monkeypatch.setattr(DB, "save", mock_save)

        await manager.setup()

        # Empty array should NOT be written to DB
        assert manager._settings_db_key() not in saved

    @pytest.mark.asyncio
    async def test_seed_empty_string_skipped(self, monkeypatch):
        """setup() skips CCAT_MCP_SERVERS="" — falsy value."""
        manager = MCPServerManager()
        manager.plugin_id = "core"

        monkeypatch.setenv("CCAT_MCP_SERVERS", "")

        from cat.db import DB

        saved = {}

        async def mock_save(key, value):
            saved[key] = value

        monkeypatch.setattr(DB, "save", mock_save)

        await manager.setup()

        assert manager._settings_db_key() not in saved

    @pytest.mark.asyncio
    async def test_seed_db_load_fails_gracefully(self, monkeypatch):
        """setup() does not crash when DB.load() raises."""
        manager = MCPServerManager()
        manager.plugin_id = "core"

        monkeypatch.setenv(
            "CCAT_MCP_SERVERS",
            '[{"name":"ok","description":"OK","url":"http://ok:8000/mcp"}]',
        )

        from cat.db import DB

        saved = {}

        async def mock_load(key):
            raise ConnectionError("DB unavailable")

        async def mock_save(key, value):
            saved[key] = value

        monkeypatch.setattr(DB, "load", mock_load)
        monkeypatch.setattr(DB, "save", mock_save)

        # Should not raise
        await manager.setup()

        # Nothing saved — seed aborted due to DB error
        assert manager._settings_db_key() not in saved

    @pytest.mark.asyncio
    async def test_seed_then_reboot_skips(self, monkeypatch):
        """First boot seeds, second boot sees DB data and skips.

        Simulates the two-boot sequence: env var seeds on first boot,
        then on reboot the DB already has data so env var is ignored.
        """
        from cat.db import DB

        manager = MCPServerManager()
        manager.plugin_id = "core"

        monkeypatch.setenv(
            "CCAT_MCP_SERVERS",
            '[{"name":"seeded","description":"From env","url":"http://seeded:8000/mcp"}]',
        )

        # --- First boot: DB is empty ---
        db = {}
        saved_count = 0

        async def mock_load_empty(key):
            return None

        async def mock_save(key, value):
            nonlocal saved_count
            saved_count += 1
            db[key] = value

        monkeypatch.setattr(DB, "load", mock_load_empty)
        monkeypatch.setattr(DB, "save", mock_save)

        await manager.setup()

        assert saved_count == 1
        assert len(db[manager._settings_db_key()]["servers"]) == 1

        # --- Second boot: DB has data ---
        async def mock_load_seeded(key):
            return db.get(key)

        monkeypatch.setattr(DB, "load", mock_load_seeded)

        await manager.setup()

        # Should NOT save again — DB already has data
        assert saved_count == 1

    @pytest.mark.asyncio
    async def test_seed_db_save_fails_gracefully(self, monkeypatch):
        """setup() logs error when DB.save() fails but does not crash."""
        manager = MCPServerManager()
        manager.plugin_id = "core"

        monkeypatch.setenv(
            "CCAT_MCP_SERVERS",
            '[{"name":"ok","description":"OK","url":"http://ok:8000/mcp"}]',
        )

        from cat.db import DB

        async def mock_load(key):
            return None

        async def mock_save(key, value):
            raise ConnectionError("DB write failed")

        monkeypatch.setattr(DB, "load", mock_load)
        monkeypatch.setattr(DB, "save", mock_save)

        # Should not raise — error caught by the outer try/except
        await manager.setup()


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
