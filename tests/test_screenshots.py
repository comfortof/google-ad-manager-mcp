"""Tests for campaign evidence screenshots."""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client

import gam_mcp.server as server
from gam_mcp.tools import screenshots


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
    """The headless browser only opens public http(s) pages."""

    @pytest.mark.parametrize("url", [
        "https://www.example.com/news/business/a-123",
        "http://example.com/page?x=1",
    ])
    def test_accepts_public_urls(self, url, monkeypatch):
        monkeypatch.delenv(screenshots.ALLOWED_HOSTS_ENV, raising=False)
        assert screenshots.validate_site_url(url) == url

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
    def test_rejects_unsafe_urls(self, url, monkeypatch):
        monkeypatch.delenv(screenshots.ALLOWED_HOSTS_ENV, raising=False)
        with pytest.raises(screenshots.EvidenceError):
            screenshots.validate_site_url(url)

    def test_allowed_hosts_include_subdomains(self, monkeypatch):
        monkeypatch.setenv(screenshots.ALLOWED_HOSTS_ENV, "example.com, example.org")
        screenshots.validate_site_url("https://www.example.com/a")
        screenshots.validate_site_url("https://example.com/a")
        with pytest.raises(screenshots.EvidenceError, match="not in"):
            screenshots.validate_site_url("https://evilexample.com/a")


class TestPlanCaptures:
    """Collecting creatives and preview URLs from GAM."""

    @patch("gam_mcp.tools.screenshots.get_gam_client")
    def test_order_lists_active_creatives_with_preview_urls(self, mock_get_client, monkeypatch):
        monkeypatch.delenv(screenshots.SITE_URL_ENV, raising=False)
        mock_client, services = _setup(mock_get_client, LINE_ITEMS, LICAS, CREATIVES)

        plan = screenshots.plan_captures(
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

    @patch("gam_mcp.tools.screenshots.get_gam_client")
    def test_line_item_finds_its_order(self, mock_get_client):
        mock_client, _ = _setup(mock_get_client, LINE_ITEMS, LICAS[:1], CREATIVES)

        plan = screenshots.plan_captures(line_item_id=7, site_url="https://www.example.com/a")

        assert plan["order"]["id"] == 42
        mock_client.create_statement.return_value.Where.assert_any_call("id = :id")

    @patch("gam_mcp.tools.screenshots.get_gam_client")
    def test_missing_site_url_is_reported_per_creative(self, mock_get_client, monkeypatch):
        monkeypatch.delenv(screenshots.SITE_URL_ENV, raising=False)
        _, services = _setup(mock_get_client, LINE_ITEMS, LICAS, CREATIVES)

        plan = screenshots.plan_captures(order_id=42, site_urls={"728x90": "https://www.example.com/top"})

        mpu = plan["items"][1]
        assert "No site URL for size 300x250" in mpu["error"]
        assert "preview_url" not in mpu
        assert services['LineItemCreativeAssociationService'].getPreviewUrl.call_count == 1

    @patch("gam_mcp.tools.screenshots.get_gam_client")
    def test_site_url_env_default(self, mock_get_client, monkeypatch):
        monkeypatch.setenv(screenshots.SITE_URL_ENV, "https://www.example.com/default")
        _setup(mock_get_client, LINE_ITEMS, LICAS[:1], CREATIVES)

        plan = screenshots.plan_captures(order_id=42)

        assert plan["items"][0]["site_url"] == "https://www.example.com/default"

    @patch("gam_mcp.tools.screenshots.get_gam_client")
    def test_preview_errors_do_not_stop_other_creatives(self, mock_get_client):
        def preview(li, cr, url):
            if cr == 101:
                raise Exception("NOT_FOUND")
            return f"{url}?google_preview=x"

        _setup(mock_get_client, LINE_ITEMS, LICAS, CREATIVES, preview=preview)

        items = screenshots.plan_captures(order_id=42, site_url="https://www.example.com/a")["items"]

        assert items[0]["error"] == "NOT_FOUND"
        assert "preview_url" in items[1]

    @patch("gam_mcp.tools.screenshots.get_gam_client")
    def test_no_line_items(self, mock_get_client):
        _setup(mock_get_client, [], [], [])

        assert screenshots.plan_captures(order_id=1) == {"error": "No line items found for order 1"}


class TestCaptureArguments:
    """Bad arguments are rejected before GAM or the browser is touched."""

    @pytest.mark.parametrize("kwargs, message", [
        ({}, "exactly one"),
        ({"order_id": 1, "line_item_id": 2}, "exactly one"),
        ({"order_id": 1, "viewports": ["tablet"]}, "Unknown viewports"),
        ({"order_id": 1, "site_url": "http://127.0.0.1/"}, "IP address"),
        ({"order_id": 1, "site_urls": {"300x250": "file:///etc/passwd"}}, "http(s)"),
    ])
    @patch("gam_mcp.tools.screenshots.plan_captures")
    async def test_rejected(self, mock_plan, kwargs, message, tmp_path, monkeypatch):
        monkeypatch.setenv(screenshots.EVIDENCE_DIR_ENV, str(tmp_path))
        result = await screenshots.capture_campaign_screenshots(**kwargs)

        assert message in result["error"]
        mock_plan.assert_not_called()


class TestServerTool:
    """The MCP tool is read-only and stays available in GAM_READ_ONLY mode."""

    async def test_available_in_read_only_mode(self):
        server.enable_read_only_mode()
        try:
            async with Client(server.mcp) as client:
                tools = {t.name: t for t in await client.list_tools()}
        finally:
            server.mcp.enable(tags={server.WRITE_TAG})

        tool = tools["capture_campaign_screenshots"]
        assert "ctx" not in tool.input_schema["properties"]

    @patch("gam_mcp.server.init_client")
    @patch("gam_mcp.server.screenshots.capture_campaign_screenshots")
    async def test_passes_arguments(self, mock_capture, mock_init):
        mock_capture.return_value = {"summary": {"captures": 0}}
        async with Client(server.mcp) as client:
            result = await client.call_tool(
                "capture_campaign_screenshots",
                {"order_id": 42, "site_urls": {"300x250": "https://www.example.com/a"}, "viewports": ["mobile"]},
            )

        assert json.loads(result.content[0].text) == {"summary": {"captures": 0}}
        kwargs = mock_capture.call_args.kwargs
        assert kwargs["order_id"] == 42
        assert kwargs["site_urls"] == {"300x250": "https://www.example.com/a"}
        assert kwargs["viewports"] == ["mobile"]


# A page with a stub GPT: the slot sits below the fold and only "renders" the
# previewed creative once scrolled near, behind a consent banner.
FAKE_PAGE = """<!doctype html><html><body style="margin:0">
<div id="onetrust-banner-sdk" style="position:fixed;bottom:0;width:100%;background:#eee;z-index:9">
  <button id="onetrust-accept-btn-handler" onclick="this.parentNode.remove()">Accept</button>
</div>
<div style="height:2500px;background:linear-gradient(#fff,#ccd)">Article text</div>
<div id="div-gpt-ad-1" style="width:300px;height:250px"></div>
<div style="height:1500px"></div>
<script>
  let rendered = false;
  const slot = {
    getSlotElementId: () => 'div-gpt-ad-1',
    getResponseInformation: () => rendered ? {lineItemId: 7, creativeId: 101} : null,
  };
  window.googletag = {pubads: () => ({getSlots: () => [slot]})};
  addEventListener('scroll', () => {
    const el = document.getElementById('div-gpt-ad-1');
    if (!rendered && el.getBoundingClientRect().top < innerHeight * 1.5) {
      rendered = true;
      el.style.background = '#e33';
      el.textContent = 'Creative 101';
    }
  });
</script></body></html>"""


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _chromium_available(), reason="Playwright Chromium not installed")
class TestCaptureInBrowser:
    """Runs the real capture against a local page in headless Chromium."""

    async def test_captures_slot_page_and_pdf(self, tmp_path, monkeypatch):
        page = tmp_path / "site" / "article.html"
        page.parent.mkdir()
        page.write_text(FAKE_PAGE)
        evidence = tmp_path / "evidence"
        monkeypatch.setenv(screenshots.EVIDENCE_DIR_ENV, str(evidence))

        plan = {
            "order": {"id": 42, "name": "Acme <test>"},
            "items": [
                {
                    "line_item_id": 7, "line_item_name": "LI", "creative_id": 101,
                    "creative_name": "MPU", "size": "300x250",
                    "site_url": "https://www.example.com/a", "preview_url": page.as_uri(),
                },
                {
                    "line_item_id": 7, "line_item_name": "LI", "creative_id": 102,
                    "creative_name": "Other", "size": "970x250",
                    "site_url": None, "error": "No site URL for size 970x250",
                },
            ],
        }
        progress = []

        async def on_progress(done, total):
            progress.append((done, total))

        with patch("gam_mcp.tools.screenshots.plan_captures", return_value=plan):
            result = await screenshots.capture_campaign_screenshots(
                order_id=42, on_progress=on_progress,
            )

        assert result["summary"] == {
            "creatives": 2, "skipped": 1, "captures": 2, "slot_found": 2, "failed": 0,
        }
        assert progress == [(1, 2), (2, 2)]
        out = evidence.resolve() / "42" / "7"
        for viewport in ("desktop", "mobile"):
            assert (out / f"101_{viewport}.png").read_bytes().startswith(b"\x89PNG")
            assert (out / f"101_{viewport}_slot.png").read_bytes().startswith(b"\x89PNG")
        captures = result["items"][0]["captures"]
        assert all(c["consent_accepted"] for c in captures)
        assert result["pdf"] == str(evidence.resolve() / "42" / "evidence.pdf")
        assert (evidence / "42" / "evidence.pdf").read_bytes().startswith(b"%PDF")
        assert "preview_url" not in result["items"][0]

    async def test_missing_slot_still_saves_page(self, tmp_path, monkeypatch):
        page = tmp_path / "plain.html"
        page.write_text("<html><body><p>No ads here</p></body></html>")
        monkeypatch.setenv(screenshots.EVIDENCE_DIR_ENV, str(tmp_path / "evidence"))
        plan = {
            "order": {"id": 1, "name": "O"},
            "items": [{
                "line_item_id": 2, "line_item_name": "LI", "creative_id": 3,
                "creative_name": "C", "size": "300x250",
                "site_url": "https://x.example/", "preview_url": page.as_uri(),
            }],
        }

        with patch("gam_mcp.tools.screenshots.plan_captures", return_value=plan):
            result = await screenshots.capture_campaign_screenshots(order_id=1, viewports=["desktop"])

        capture = result["items"][0]["captures"][0]
        assert capture["slot_found"] is False
        assert "warning" in capture
        assert capture["page_png"].endswith("3_desktop.png")
