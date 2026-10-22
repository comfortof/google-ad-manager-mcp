"""Tests for GAM_READ_ONLY mode."""

from unittest.mock import patch

import pytest
from fastmcp import Client

import gam_mcp.server as server

WRITE_TOOLS = {
    "create_order", "find_or_create_order",
    "create_line_item", "duplicate_line_item", "update_line_item",
    "pause_line_item", "resume_line_item", "archive_line_item", "approve_line_item",
    "upload_creative", "associate_creative_with_line_item", "upload_and_associate_creative",
    "bulk_upload_creatives", "update_creative", "create_third_party_creative",
    "create_advertiser", "find_or_create_advertiser",
    "create_campaign",
}

# Name prefixes that signal a mutation; any tool starting with one must be tagged
WRITE_PREFIXES = (
    "create_", "find_or_create_", "update_", "duplicate_", "delete_", "remove_",
    "pause_", "resume_", "archive_", "approve_", "upload_", "associate_", "bulk_",
    "apply_", "exclude_", "set_",
)


async def _tools():
    async with Client(server.mcp) as client:
        return {tool.name: tool for tool in await client.list_tools()}


@pytest.fixture
def read_only_mcp():
    """Enable read-only mode on the real server, then restore it."""
    server.enable_read_only_mode()
    yield server.mcp
    server.mcp.enable(tags={server.WRITE_TAG})


class TestWriteTags:
    """Every tool that changes Ad Manager must carry the write tag."""

    async def test_write_tools_are_tagged(self):
        tools = await _tools()
        tagged = {name for name, tool in tools.items() if server.WRITE_TAG in (tool.meta or {}).get("fastmcp", {}).get("tags", [])}
        assert tagged == WRITE_TOOLS

    async def test_tools_named_like_writes_are_tagged(self):
        untagged_writes = {name for name in await _tools() if name.startswith(WRITE_PREFIXES)} - WRITE_TOOLS
        assert not untagged_writes, f"Tag these tools with WRITE_TAG: {sorted(untagged_writes)}"


class TestReadOnlyMode:
    """Read-only mode hides write tools and refuses to run them."""

    async def test_write_tools_are_not_listed(self, read_only_mcp):
        tools = await _tools()

        assert not WRITE_TOOLS & tools.keys()
        assert {"get_delivery_progress", "get_line_item", "run_delivery_report"} <= tools.keys()

    @patch("gam_mcp.server.init_client")
    @patch("gam_mcp.server.line_items.pause_line_item")
    async def test_write_tools_cannot_be_called(self, mock_pause, mock_init, read_only_mcp):
        async with Client(read_only_mcp) as client:
            with pytest.raises(Exception, match="pause_line_item"):
                await client.call_tool("pause_line_item", {"line_item_id": 1})

        mock_pause.assert_not_called()

    @patch("gam_mcp.server.init_client")
    @patch("gam_mcp.server.delivery.get_delivery_progress", return_value={"line_items": []})
    async def test_read_tools_still_work(self, mock_progress, mock_init, read_only_mcp):
        async with Client(read_only_mcp) as client:
            result = await client.call_tool("get_delivery_progress", {"order_id": 1})

        assert '"line_items": []' in result.content[0].text

    async def test_write_tools_listed_by_default(self):
        assert WRITE_TOOLS <= (await _tools()).keys()


class TestReadOnlyRequested:
    """GAM_READ_ONLY parsing."""

    @pytest.mark.parametrize("value", ["true", "TRUE", " 1 ", "yes", "on"])
    def test_true_values(self, value):
        assert server.read_only_requested(value) is True

    @pytest.mark.parametrize("value", [None, "", "false", "0", "no", "off"])
    def test_false_values(self, value):
        assert server.read_only_requested(value) is False

    @pytest.mark.parametrize("value", ["ture", "enabled", "2"])
    def test_unrecognised_values_raise(self, value):
        with pytest.raises(ValueError, match="GAM_READ_ONLY"):
            server.read_only_requested(value)


class TestMainReadOnly:
    """main() applies GAM_READ_ONLY before serving on any transport."""

    @patch("gam_mcp.server.mcp")
    def test_stdio_read_only(self, mock_mcp):
        with patch.dict("os.environ", {"GAM_READ_ONLY": "true"}, clear=True):
            server.main()

        mock_mcp.disable.assert_called_once_with(tags={server.WRITE_TAG})
        mock_mcp.run.assert_called_once_with(transport="stdio")

    @patch("gam_mcp.server.mcp")
    def test_http_read_only(self, mock_mcp, monkeypatch):
        monkeypatch.setattr(server, "AUTH_TOKEN", None)
        monkeypatch.setattr(server, "HTTP_MODE", False)
        env = {"GAM_READ_ONLY": "1", "GAM_MCP_TRANSPORT": "http", "GAM_MCP_AUTH_TOKEN": "a" * 64}
        with patch.dict("os.environ", env, clear=True):
            server.main()

        mock_mcp.disable.assert_called_once_with(tags={server.WRITE_TAG})
        mock_mcp.run.assert_called_once()

    @patch("gam_mcp.server.mcp")
    def test_default_keeps_write_tools(self, mock_mcp):
        with patch.dict("os.environ", {}, clear=True):
            server.main()

        mock_mcp.disable.assert_not_called()

    @patch("gam_mcp.server.mcp")
    def test_invalid_value_refuses_to_start(self, mock_mcp):
        with patch.dict("os.environ", {"GAM_READ_ONLY": "ture"}, clear=True):
            with pytest.raises(SystemExit, match="GAM_READ_ONLY"):
                server.main()

        mock_mcp.run.assert_not_called()
