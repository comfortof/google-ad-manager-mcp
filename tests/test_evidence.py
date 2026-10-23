"""Tests for the local gam-evidence screenshot command."""

import json
import socket
from unittest.mock import AsyncMock, patch

import pytest

from gam_mcp import evidence
from gam_mcp.tools import previews

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

    @pytest.fixture(autouse=True)
    def allow_file_urls(self, monkeypatch):
        # Real plans only carry http(s) previews; these tests use local pages
        monkeypatch.setattr(evidence, "validate_site_url", lambda url: url)

    async def test_captures_slot_page_and_pdf(self, tmp_path):
        page = tmp_path / "site" / "article.html"
        page.parent.mkdir()
        page.write_text(FAKE_PAGE)
        evidence_dir = tmp_path / "evidence"

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

        result = await evidence.capture(plan, evidence_dir, on_progress=on_progress)

        assert result["summary"] == {
            "creatives": 2, "skipped": 1, "captures": 2, "slot_found": 2, "failed": 0,
        }
        assert progress == [(1, 2), (2, 2)]
        out = evidence_dir.resolve() / "42" / "7"
        for viewport in ("desktop", "mobile"):
            assert (out / f"101_{viewport}.png").read_bytes().startswith(b"\x89PNG")
            assert (out / f"101_{viewport}_slot.png").read_bytes().startswith(b"\x89PNG")
        captures = result["items"][0]["captures"]
        assert all(c["consent_accepted"] for c in captures)
        assert result["pdf"] == str(evidence_dir.resolve() / "42" / "evidence.pdf")
        assert (evidence_dir / "42" / "evidence.pdf").read_bytes().startswith(b"%PDF")
        assert "preview_url" not in result["items"][0]

    async def test_missing_slot_still_saves_page(self, tmp_path):
        page = tmp_path / "plain.html"
        page.write_text("<html><body><p>No ads here</p></body></html>")
        plan = {
            "order": {"id": 1, "name": "O"},
            "items": [{
                "line_item_id": 2, "line_item_name": "LI", "creative_id": 3,
                "creative_name": "C", "size": "300x250",
                "site_url": "https://x.example/", "preview_url": page.as_uri(),
            }],
        }

        result = await evidence.capture(plan, tmp_path / "evidence", ["desktop"])

        capture = result["items"][0]["captures"][0]
        assert capture["slot_found"] is False
        assert "warning" in capture
        assert capture["diagnostics"] == {
            "gpt_loaded": False, "preview_in_url": False, "final_url": "file://" + str(page),
            "slots_total": 0, "slots_rendered": 0, "slots_with_size": 0, "slots": [],
        }
        assert capture["page_png"].endswith("3_desktop.png")

    async def test_unsafe_preview_url_is_skipped(self, tmp_path):
        plan = {
            "order": {"id": 1, "name": "O"},
            "items": [{
                "line_item_id": 2, "line_item_name": "LI", "creative_id": 3,
                "creative_name": "C", "size": "300x250",
                "site_url": "https://x.example/", "preview_url": "http://127.0.0.1/",
            }],
        }
        # Undo the class's file:// allowance: this test needs the real check
        with patch.object(evidence, "validate_site_url", previews.validate_site_url):
            result = await evidence.capture(plan, tmp_path / "evidence", ["desktop"])

        assert result["summary"]["skipped"] == 1
        assert "IP address" in result["items"][0]["error"]
        assert result["pdf"] is None


class TestArguments:
    """Command-line parsing."""

    def test_site_url_per_size(self):
        args = evidence.build_parser().parse_args([
            "--order", "42", "--site-url-for", "300x250=https://www.example.com/a",
            "--site-url-for", "970x250=https://www.example.com/b", "--viewport", "mobile",
        ])
        assert dict(args.site_url_for) == {
            "300x250": "https://www.example.com/a", "970x250": "https://www.example.com/b",
        }
        assert args.viewport == ["mobile"]

    @pytest.mark.parametrize("argv", [
        [],
        ["--order", "1", "--line-item", "2"],
        ["--order", "1", "--site-url-for", "https://www.example.com/a"],
        ["--order", "1", "--viewport", "tablet"],
    ])
    def test_rejected(self, argv):
        with pytest.raises(SystemExit):
            evidence.build_parser().parse_args(argv)

    def test_server_from_env(self, monkeypatch):
        monkeypatch.setenv(evidence.SERVER_URL_ENV, "https://gam.example/mcp")
        assert evidence.build_parser().parse_args(["--order", "1"]).server == "https://gam.example/mcp"


class TestAdBlockDetection:
    """A DNS sinkhole for Google's ad hosts is caught before any capture."""

    @pytest.mark.parametrize("address, blocked", [
        ("0.0.0.0", True),
        ("127.0.0.1", True),
        ("::", True),
        ("142.250.130.156", False),
    ])
    def test_sinkhole_addresses(self, address, blocked):
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        info = [(family, socket.SOCK_STREAM, 6, "", (address, 443))]
        with patch("socket.getaddrinfo", return_value=info):
            assert evidence.ad_hosts_blocked() is blocked

    def test_resolution_failure_counts_as_blocked(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror):
            assert evidence.ad_hosts_blocked() is True


class TestRun:
    """The command fetches the plan from the server, then captures locally."""

    def _args(self, *extra):
        return evidence.build_parser().parse_args(
            ["--order", "42", "--site-url", "https://www.example.com/a", "--server", "http://s/mcp", *extra]
        )

    async def test_blocked_dns_stops_before_the_server(self, capsys):
        with patch.object(evidence, "ad_hosts_blocked", return_value=True), \
                patch.object(evidence, "fetch_plan", new=AsyncMock()) as fetch:
            assert await evidence._run(self._args()) == 1
        fetch.assert_not_called()
        assert "--public-dns" in capsys.readouterr().err

    async def test_plan_and_capture(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv(evidence.TOKEN_ENV, "secret-token")
        plan = {"order": {"id": 42, "name": "O"}, "items": []}
        result = {"evidence_dir": str(tmp_path / "42"), "summary": {"captures": 0}}
        with patch.object(evidence, "ad_hosts_blocked", return_value=False), \
                patch.object(evidence, "fetch_plan", new=AsyncMock(return_value=plan)) as fetch, \
                patch.object(evidence, "capture", new=AsyncMock(return_value=result)) as capture:
            assert await evidence._run(self._args("--out", str(tmp_path))) == 0

        fetch.assert_awaited_once_with(
            "http://s/mcp", "secret-token", {"order_id": 42, "site_url": "https://www.example.com/a"},
        )
        assert capture.await_args.args[0] is plan
        assert json.loads(capsys.readouterr().out) == result
        assert json.loads((tmp_path / "42" / "summary.json").read_text()) == result

    async def test_public_dns_maps_ad_hosts(self):
        with patch.object(evidence, "public_dns_rules", return_value="MAP *.doubleclick.net 1.2.3.4"), \
                patch.object(evidence, "fetch_plan", new=AsyncMock(return_value={"error": "x"})), \
                patch.object(evidence, "ad_hosts_blocked") as blocked:
            assert await evidence._run(self._args("--public-dns")) == 1
        blocked.assert_not_called()

    async def test_plan_error(self, capsys):
        with patch.object(evidence, "ad_hosts_blocked", return_value=False), \
                patch.object(evidence, "fetch_plan", new=AsyncMock(return_value={"error": "No line items found for order 42"})):
            assert await evidence._run(self._args()) == 1
        assert "No line items" in capsys.readouterr().err

    async def test_server_unreachable(self, capsys):
        with patch.object(evidence, "ad_hosts_blocked", return_value=False), \
                patch.object(evidence, "fetch_plan", new=AsyncMock(side_effect=RuntimeError("401"))):
            assert await evidence._run(self._args()) == 1
        assert evidence.TOKEN_ENV in capsys.readouterr().err
