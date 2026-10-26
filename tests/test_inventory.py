"""Tests for ad unit discovery tools."""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client

import gam_mcp.server as server
from gam_mcp.tools import inventory

NETWORK = "123456"

ROOT = {"id": "1", "name": "Network", "adUnitCode": "ca-pub-1", "parentPath": [], "hasChildren": True}
SECTION = {
    "id": "10", "name": "News", "adUnitCode": "news", "parentId": 1, "hasChildren": True,
    "status": "ACTIVE", "parentPath": [{"id": 1, "name": "Network", "adUnitCode": "ca-pub-1"}],
}
SLOT = {
    "id": "100", "name": "News MPU", "adUnitCode": "news_mpu", "parentId": 10, "status": "ACTIVE",
    "description": "Article sidebar",
    "parentPath": [
        {"id": 1, "name": "Network", "adUnitCode": "ca-pub-1"},
        {"id": 10, "name": "News", "adUnitCode": "news"},
    ],
    "adUnitSizes": [
        {"size": {"width": 300, "height": 250}, "fullDisplayString": "300x250"},
        {"size": {"width": 300, "height": 600}},
    ],
}


def _client(mock_get_client, ad_units=(), line_items=(), placements=()):
    client = MagicMock()
    client.network_code = NETWORK
    mock_get_client.return_value = client
    services = {name: MagicMock() for name in ('InventoryService', 'LineItemService', 'PlacementService')}
    client.get_service.side_effect = services.__getitem__
    services['InventoryService'].getAdUnitsByStatement.return_value = {"results": list(ad_units)}
    services['LineItemService'].getLineItemsByStatement.return_value = {"results": list(line_items)}
    services['PlacementService'].getPlacementsByStatement.return_value = {"results": list(placements)}
    return client


class TestGptPath:
    """Ad unit paths as pages pass them to GPT."""

    @pytest.mark.parametrize("unit, path", [
        (ROOT, "/123456"),
        (SECTION, "/123456/news"),
        (SLOT, "/123456/news/news_mpu"),
    ])
    def test_paths(self, unit, path):
        assert inventory.gpt_path(unit, NETWORK) == path


class TestFindAdUnits:

    @patch("gam_mcp.tools.inventory.get_gam_client")
    def test_entry_fields(self, mock_get_client):
        _client(mock_get_client, ad_units=[SLOT])

        result = inventory.find_ad_units(ad_unit_id=100)

        assert result["count"] == 1
        assert result["ad_units"][0] == {
            "id": "100", "name": "News MPU", "code": "news_mpu", "gpt_path": "/123456/news/news_mpu",
            "name_path": "Network > News > News MPU", "is_root": False, "parent_id": 10,
            "has_children": False, "status": "ACTIVE", "description": "Article sidebar",
            "sizes": ["300x250", "300x600"], "target_window": None, "explicitly_targeted": False,
        }

    @patch("gam_mcp.tools.inventory.get_gam_client")
    def test_filters_are_bound(self, mock_get_client):
        client = _client(mock_get_client, ad_units=[SECTION])
        statement = client.create_statement.return_value
        statement.Where.return_value = statement.WithBindVariable.return_value = statement

        inventory.find_ad_units(name="New", parent_id=1)

        statement.Where.assert_called_once_with("name LIKE :name AND parentId = :parentId AND status = :status")
        binds = dict(c.args for c in statement.WithBindVariable.call_args_list)
        assert binds == {"name": "%New%", "parentId": 1, "status": "ACTIVE"}

    @patch("gam_mcp.tools.inventory.get_gam_client")
    def test_limit_and_sorting(self, mock_get_client):
        _client(mock_get_client, ad_units=[SLOT, ROOT, SECTION])

        result = inventory.find_ad_units(status=None, limit=2)

        assert [u["gpt_path"] for u in result["ad_units"]] == ["/123456", "/123456/news"]
        assert result["truncated"] is True

    @patch("gam_mcp.tools.inventory.get_gam_client")
    def test_bad_status(self, mock_get_client):
        assert "status" in inventory.find_ad_units(status="LIVE")["error"]
        mock_get_client.assert_not_called()


class TestLineItemInventory:

    @patch("gam_mcp.tools.inventory.get_gam_client")
    def test_resolves_targeting(self, mock_get_client):
        line_item = {
            "id": 7, "name": "LI", "orderId": 42,
            "creativePlaceholders": [{"size": {"width": 300, "height": 250}}],
            "targeting": {"inventoryTargeting": {
                "targetedAdUnits": [{"adUnitId": "10", "includeDescendants": True}],
                "excludedAdUnits": [{"adUnitId": "100", "includeDescendants": False}],
                "targetedPlacementIds": [5],
            }},
        }
        placement = {"id": 5, "name": "Sidebars", "targetedAdUnitIds": ["100"]}
        _client(mock_get_client, ad_units=[SECTION, SLOT], line_items=[line_item], placements=[placement])

        result = inventory.get_line_item_inventory(7)

        assert result["sizes"] == ["300x250"]
        assert result["run_of_network"] is False
        assert [(u["gpt_path"], u["include_descendants"]) for u in result["targeted_ad_units"]] == [
            ("/123456/news", True),
        ]
        assert [(u["gpt_path"], u["include_descendants"]) for u in result["excluded_ad_units"]] == [
            ("/123456/news/news_mpu", False),
        ]
        assert result["targeted_placements"][0]["ad_units"][0]["gpt_path"] == "/123456/news/news_mpu"

    @patch("gam_mcp.tools.inventory.get_gam_client")
    def test_root_is_run_of_network(self, mock_get_client):
        line_item = {"id": 7, "targeting": {"inventoryTargeting": {
            "targetedAdUnits": [{"adUnitId": "1", "includeDescendants": True}],
        }}}
        _client(mock_get_client, ad_units=[ROOT], line_items=[line_item])

        assert inventory.get_line_item_inventory(7)["run_of_network"] is True

    @patch("gam_mcp.tools.inventory.get_gam_client")
    def test_missing_line_item(self, mock_get_client):
        _client(mock_get_client)
        assert "not found" in inventory.get_line_item_inventory(7)["error"]


class TestAdUnitPages:

    @patch("gam_mcp.tools.inventory.get_gam_client")
    @patch("gam_mcp.tools.inventory.run_custom_report")
    def test_rows_sorted_by_impressions(self, mock_report, mock_get_client):
        _client(mock_get_client, ad_units=[SLOT])
        mock_report.return_value = {
            "success": True,
            "headers": ["Dimension.SITE_NAME", "Column.TOTAL_LINE_ITEM_LEVEL_IMPRESSIONS"],
            "data": [["a.example", "10"], ["b.example", "300"], ["c.example", "-"]],
        }

        result = inventory.get_ad_unit_pages(100, limit=2)

        kwargs = mock_report.call_args.kwargs
        assert kwargs["dimensions"] == ["SITE_NAME"]
        assert kwargs["filter_statement"] == "PARENT_AD_UNIT_ID = 100"
        assert result["gpt_path"] == "/123456/news/news_mpu"
        assert result["rows"] == [
            {"site": "b.example", "impressions": 300}, {"site": "a.example", "impressions": 10},
        ]
        assert result["truncated"] is True
        assert len(result["notes"]) == 1

    @patch("gam_mcp.tools.inventory.get_gam_client")
    @patch("gam_mcp.tools.inventory.run_custom_report")
    def test_root_with_descendants_is_unfiltered(self, mock_report, mock_get_client):
        # GAM's PARENT_AD_UNIT_ID filter returns no rows for the root ad unit
        _client(mock_get_client, ad_units=[ROOT])
        mock_report.return_value = {"headers": ["Dimension.SITE_NAME", "Column.TOTAL_LINE_ITEM_LEVEL_IMPRESSIONS"]}

        inventory.get_ad_unit_pages(1)
        assert mock_report.call_args.kwargs["filter_statement"] is None

        inventory.get_ad_unit_pages(1, include_descendants=False)
        assert mock_report.call_args.kwargs["filter_statement"] == "AD_UNIT_ID = 1"

    @patch("gam_mcp.tools.inventory.get_gam_client")
    @patch("gam_mcp.tools.inventory.run_custom_report")
    def test_url_without_descendants_carries_caveat(self, mock_report, mock_get_client):
        _client(mock_get_client, ad_units=[SLOT])
        mock_report.return_value = {
            "headers": ["Dimension.URL_ID", "Dimension.URL_NAME", "Column.TOTAL_LINE_ITEM_LEVEL_IMPRESSIONS"],
            "data": [["9", "https://x.example/a", "5"]],
        }

        result = inventory.get_ad_unit_pages(100, by="url", include_descendants=False)

        assert mock_report.call_args.kwargs["filter_statement"] == "AD_UNIT_ID = 100"
        assert result["rows"] == [{"url": "https://x.example/a", "impressions": 5}]
        assert "Ad Exchange" in result["notes"][1]

    @patch("gam_mcp.tools.inventory.get_gam_client")
    @patch("gam_mcp.tools.inventory.run_custom_report")
    def test_errors(self, mock_report, mock_get_client):
        # DOMAIN is rejected by GAM with line item level impressions
        assert "by must be" in inventory.get_ad_unit_pages(100, by="DOMAIN")["error"]
        _client(mock_get_client)
        assert "not found" in inventory.get_ad_unit_pages(100)["error"]
        mock_report.assert_not_called()

        _client(mock_get_client, ad_units=[SLOT])
        mock_report.return_value = {"error": "Failed to run report: x"}
        assert inventory.get_ad_unit_pages(100)["error"] == "Failed to run report: x"


class TestServerTools:
    """The inventory tools are read-only and stay available in GAM_READ_ONLY mode."""

    async def test_available_in_read_only_mode(self):
        server.enable_read_only_mode()
        try:
            async with Client(server.mcp) as client:
                names = {t.name for t in await client.list_tools()}
        finally:
            server.mcp.enable(tags={server.WRITE_TAG})

        assert {"find_ad_units", "get_line_item_inventory", "get_ad_unit_pages"} <= names

    @patch("gam_mcp.server.init_client")
    @patch("gam_mcp.server.inventory.find_ad_units")
    async def test_passes_arguments(self, mock_find, mock_init):
        mock_find.return_value = {"count": 0, "ad_units": []}
        async with Client(server.mcp) as client:
            result = await client.call_tool("find_ad_units", {"name": "Default", "status": None})

        assert json.loads(result.content[0].text) == {"count": 0, "ad_units": []}
        assert mock_find.call_args.kwargs["name"] == "Default"
        assert mock_find.call_args.kwargs["status"] is None
