"""Tests for the preview plan behind evidence screenshots."""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client

import gam_mcp.server as server
from gam_mcp.tools import previews


def _setup(mock_get_client, line_items, licas, creatives, orders=None, preview=None):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    services = {name: MagicMock() for name in (
        'LineItemService', 'LineItemCreativeAssociationService', 'CreativeService', 'OrderService',
    )}
    mock_client.get_service.side_effect = services.__getitem__
    services['LineItemService'].getLineItemsByStatement.return_value = {"results": line_items}
    lica_service = services['LineItemCreativeAssociationService']
    lica_service.getLineItemCreativeAssociationsByStatement.return_value = {"results": licas}
    lica_service.getPreviewUrl.side_effect = preview or (
        lambda li, cr, url: f"{url}?google_preview=x&lineItemId={li}&creativeId={cr}"
    )
    services['CreativeService'].getCreativesByStatement.return_value = {"results": creatives}
    services['OrderService'].getOrdersByStatement.return_value = {
        "results": orders if orders is not None else [{"id": 42, "name": "Acme"}]
    }
    return mock_client, services


LINE_ITEMS = [{"id": 7, "name": "Acme 300x250", "orderId": 42}]
LICAS = [
    {"lineItemId": 7, "creativeId": 102, "status": "ACTIVE"},
    {"lineItemId": 7, "creativeId": 101, "status": "ACTIVE", "sizes": [{"width": 728, "height": 90}]},
]
CREATIVES = [
    {"id": 101, "name": "Leaderboard", "size": {"width": 300, "height": 250}},
    {"id": 102, "name": "MPU", "size": {"width": 300, "height": 250}},
]


class TestValidateSiteUrl:
    """Previews only use public http(s) pages."""

    @pytest.mark.parametrize("url", [
        "https://www.example.com/news/business/a-123",
        "http://example.com/page?x=1",
    ])
    def test_accepts_public_urls(self, url):
        assert previews.validate_site_url(url) == url

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://",
        "http://127.0.0.1:8000/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:3000/",
        "http://printer.local/",
    ])
    def test_rejects_unsafe_urls(self, url):
        with pytest.raises(previews.EvidenceError):
            previews.validate_site_url(url)


class TestGetPreviewPlan:
    """Collecting creatives and preview URLs from GAM."""

    @patch("gam_mcp.tools.previews.get_gam_client")
    def test_order_lists_active_creatives_with_preview_urls(self, mock_get_client, monkeypatch):
        monkeypatch.delenv(previews.SITE_URL_ENV, raising=False)
        mock_client, services = _setup(mock_get_client, LINE_ITEMS, LICAS, CREATIVES)

        plan = previews.get_preview_plan(
            order_id=42,
            site_url="https://www.example.com/article",
            site_urls={"728x90": "https://www.example.com/top"},
        )

        assert plan["order"] == {"id": 42, "name": "Acme"}
        assert [i["creative_id"] for i in plan["items"]] == [101, 102]
        leaderboard, mpu = plan["items"]
        # The association's size override wins over the creative's own size
        assert leaderboard["size"] == "728x90"
        assert leaderboard["site_url"] == "https://www.example.com/top"
        assert mpu["site_url"] == "https://www.example.com/article"
        assert mpu["preview_url"].startswith("https://www.example.com/article?google_preview")
        services['LineItemCreativeAssociationService'].getPreviewUrl.assert_any_call(
            7, 102, "https://www.example.com/article"
        )

        where_clauses = [c.args[0] for c in mock_client.create_statement.return_value.Where.call_args_list]
        assert "orderId = :orderId" in where_clauses
        assert "lineItemId IN (7) AND status = 'ACTIVE'" in where_clauses

    @patch("gam_mcp.tools.previews.get_gam_client")
    def test_line_item_finds_its_order(self, mock_get_client):
        mock_client, _ = _setup(mock_get_client, LINE_ITEMS, LICAS[:1], CREATIVES)

        plan = previews.get_preview_plan(line_item_id=7, site_url="https://www.example.com/a")

        assert plan["order"]["id"] == 42
        mock_client.create_statement.return_value.Where.assert_any_call("id = :id")

    @patch("gam_mcp.tools.previews.get_gam_client")
    def test_missing_site_url_is_reported_per_creative(self, mock_get_client, monkeypatch):
        monkeypatch.delenv(previews.SITE_URL_ENV, raising=False)
        _, services = _setup(mock_get_client, LINE_ITEMS, LICAS, CREATIVES)

        plan = previews.get_preview_plan(order_id=42, site_urls={"728x90": "https://www.example.com/top"})

        mpu = plan["items"][1]
        assert "No site URL for size 300x250" in mpu["error"]
        assert "preview_url" not in mpu
        assert services['LineItemCreativeAssociationService'].getPreviewUrl.call_count == 1

    @patch("gam_mcp.tools.previews.get_gam_client")
    def test_site_url_env_default(self, mock_get_client, monkeypatch):
        monkeypatch.setenv(previews.SITE_URL_ENV, "https://www.example.com/default")
        _setup(mock_get_client, LINE_ITEMS, LICAS[:1], CREATIVES)

        plan = previews.get_preview_plan(order_id=42)

        assert plan["items"][0]["site_url"] == "https://www.example.com/default"

    @patch("gam_mcp.tools.previews.get_gam_client")
    def test_preview_errors_do_not_stop_other_creatives(self, mock_get_client):
        def preview(li, cr, url):
            if cr == 101:
                raise Exception("NOT_FOUND")
            return f"{url}?google_preview=x"

        _setup(mock_get_client, LINE_ITEMS, LICAS, CREATIVES, preview=preview)

        items = previews.get_preview_plan(order_id=42, site_url="https://www.example.com/a")["items"]

        assert items[0]["error"] == "NOT_FOUND"
        assert "preview_url" in items[1]

    @patch("gam_mcp.tools.previews.get_gam_client")
    def test_no_line_items(self, mock_get_client):
        _setup(mock_get_client, [], [], [])

        assert previews.get_preview_plan(order_id=1) == {"error": "No line items found for order 1"}


class TestPlanArguments:
    """Bad arguments are rejected before GAM is called."""

    @pytest.mark.parametrize("kwargs, message", [
        ({}, "exactly one"),
        ({"order_id": 1, "line_item_id": 2}, "exactly one"),
        ({"order_id": 1, "site_url": "http://127.0.0.1/"}, "IP address"),
        ({"order_id": 1, "site_urls": {"300x250": "file:///etc/passwd"}}, "http(s)"),
    ])
    @patch("gam_mcp.tools.previews.get_gam_client")
    def test_rejected(self, mock_get_client, kwargs, message):
        assert message in previews.get_preview_plan(**kwargs)["error"]
        mock_get_client.assert_not_called()


class TestServerTool:
    """The MCP tool is read-only and stays available in GAM_READ_ONLY mode."""

    async def test_available_in_read_only_mode(self):
        server.enable_read_only_mode()
        try:
            async with Client(server.mcp) as client:
                names = {t.name for t in await client.list_tools()}
        finally:
            server.mcp.enable(tags={server.WRITE_TAG})

        assert "get_preview_plan" in names
        assert "capture_campaign_screenshots" not in names

    @patch("gam_mcp.server.init_client")
    @patch("gam_mcp.server.previews.get_preview_plan")
    async def test_passes_arguments(self, mock_plan, mock_init):
        mock_plan.return_value = {"order": {"id": 42}, "items": []}
        async with Client(server.mcp) as client:
            result = await client.call_tool(
                "get_preview_plan",
                {"order_id": 42, "site_urls": {"300x250": "https://www.example.com/a"}},
            )

        assert json.loads(result.content[0].text) == {"order": {"id": 42}, "items": []}
        kwargs = mock_plan.call_args.kwargs
        assert kwargs["order_id"] == 42
        assert kwargs["site_urls"] == {"300x250": "https://www.example.com/a"}
