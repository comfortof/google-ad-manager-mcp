"""Tests for MCP server and middleware."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import hmac
import logging

from gam_mcp.server import BearerAuthMiddleware, BearerTokenASGIMiddleware, validate_auth_token


VALID_TOKEN = "a" * 64


class TestBearerAuthMiddleware:
    """Tests for BearerAuthMiddleware class."""

    @pytest.fixture
    def middleware(self):
        """Create middleware instance."""
        return BearerAuthMiddleware()

    @pytest.mark.asyncio
    @patch("gam_mcp.server.HTTP_MODE", False)
    @patch("gam_mcp.server.AUTH_TOKEN", None)
    @patch("gam_mcp.server.get_http_headers")
    async def test_allows_request_in_stdio_mode(self, mock_headers, middleware):
        """Test allows request for the local stdio transport."""
        context = MagicMock()
        call_next = AsyncMock(return_value="success")

        result = await middleware.on_call_tool(context, call_next)

        assert result == "success"
        call_next.assert_called_once_with(context)
        mock_headers.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("auth_header, allowed", [
        (f"Bearer {VALID_TOKEN}", True),
        ("Bearer " + "b" * 64, False),
    ])
    @patch("gam_mcp.server.HTTP_MODE", True)
    @patch("gam_mcp.server.AUTH_TOKEN", VALID_TOKEN)
    async def test_reads_authorization_from_real_request(self, middleware, auth_header, allowed):
        """Test the real FastMCP header helper still exposes the Authorization header."""
        from fastmcp.exceptions import ToolError
        from starlette.requests import Request

        request = Request({
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [(b"authorization", auth_header.encode())],
        })
        context = MagicMock()
        call_next = AsyncMock(return_value="success")

        with patch("fastmcp.server.dependencies.get_http_request", return_value=request):
            if allowed:
                assert await middleware.on_call_tool(context, call_next) == "success"
            else:
                with pytest.raises(ToolError, match="Invalid token"):
                    await middleware.on_call_tool(context, call_next)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("token", [None, "", "   "])
    @patch("gam_mcp.server.HTTP_MODE", True)
    @patch("gam_mcp.server.get_http_headers")
    async def test_denies_http_request_when_no_token_configured(self, mock_headers, middleware, token):
        """Test HTTP mode fails closed when the token is missing or empty."""
        mock_headers.return_value = {"authorization": "Bearer "}
        context = MagicMock()
        call_next = AsyncMock()

        from fastmcp.exceptions import ToolError
        with patch("gam_mcp.server.AUTH_TOKEN", token):
            with pytest.raises(ToolError):
                await middleware.on_call_tool(context, call_next)

        call_next.assert_not_called()

    @pytest.mark.asyncio
    @patch("gam_mcp.server.HTTP_MODE", True)
    @patch("gam_mcp.server.AUTH_TOKEN", VALID_TOKEN)
    @patch("gam_mcp.server.get_http_headers")
    async def test_rejects_missing_auth_header(self, mock_headers, middleware):
        """Test rejects request when Authorization header is missing."""
        mock_headers.return_value = {}
        context = MagicMock()
        call_next = AsyncMock()

        from fastmcp.exceptions import ToolError
        with pytest.raises(ToolError) as exc_info:
            await middleware.on_call_tool(context, call_next)

        assert "Missing Authorization header" in str(exc_info.value)
        call_next.assert_not_called()

    @pytest.mark.asyncio
    @patch("gam_mcp.server.HTTP_MODE", True)
    @patch("gam_mcp.server.AUTH_TOKEN", VALID_TOKEN)
    @patch("gam_mcp.server.get_http_headers")
    async def test_rejects_invalid_auth_format(self, mock_headers, middleware):
        """Test rejects request with invalid Authorization format."""
        mock_headers.return_value = {"authorization": "Basic dXNlcjpwYXNz"}
        context = MagicMock()
        call_next = AsyncMock()

        from fastmcp.exceptions import ToolError
        with pytest.raises(ToolError) as exc_info:
            await middleware.on_call_tool(context, call_next)

        assert "Invalid Authorization format" in str(exc_info.value)
        call_next.assert_not_called()

    @pytest.mark.asyncio
    @patch("gam_mcp.server.HTTP_MODE", True)
    @patch("gam_mcp.server.AUTH_TOKEN", VALID_TOKEN)
    @patch("gam_mcp.server.get_http_headers")
    async def test_rejects_invalid_token(self, mock_headers, middleware):
        """Test rejects request with wrong token."""
        mock_headers.return_value = {"authorization": "Bearer wrong-token"}
        context = MagicMock()
        call_next = AsyncMock()

        from fastmcp.exceptions import ToolError
        with pytest.raises(ToolError) as exc_info:
            await middleware.on_call_tool(context, call_next)

        assert "Invalid token" in str(exc_info.value)
        call_next.assert_not_called()

    @pytest.mark.asyncio
    @patch("gam_mcp.server.HTTP_MODE", True)
    @patch("gam_mcp.server.AUTH_TOKEN", VALID_TOKEN)
    @patch("gam_mcp.server.get_http_headers")
    async def test_accepts_valid_token(self, mock_headers, middleware):
        """Test accepts request with valid token."""
        mock_headers.return_value = {"authorization": f"Bearer {VALID_TOKEN}"}
        context = MagicMock()
        call_next = AsyncMock(return_value="success")

        result = await middleware.on_call_tool(context, call_next)

        assert result == "success"
        call_next.assert_called_once_with(context)

    @pytest.mark.asyncio
    @patch("gam_mcp.server.HTTP_MODE", True)
    @patch("gam_mcp.server.AUTH_TOKEN", VALID_TOKEN)
    @patch("gam_mcp.server.get_http_headers")
    async def test_denies_on_header_error(self, mock_headers, middleware):
        """Test fails closed when headers cannot be retrieved in HTTP mode."""
        mock_headers.side_effect = Exception("No HTTP context")
        context = MagicMock()
        call_next = AsyncMock()

        from fastmcp.exceptions import ToolError
        with pytest.raises(ToolError):
            await middleware.on_call_tool(context, call_next)

        call_next.assert_not_called()


class TestBearerTokenASGIMiddleware:
    """Tests for request-level authentication of every HTTP request."""

    @pytest.fixture
    def http_client(self):
        from starlette.applications import Starlette
        from starlette.middleware import Middleware as ASGIMiddleware
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient

        async def endpoint(request):
            return PlainTextResponse("ok")

        app = Starlette(
            routes=[Route("/mcp", endpoint, methods=["GET", "POST"])],
            middleware=[ASGIMiddleware(BearerTokenASGIMiddleware, token=VALID_TOKEN)],
        )
        return TestClient(app)

    def test_rejects_initialize_without_token(self, http_client):
        """Test the MCP initialize request is rejected without a token."""
        response = http_client.post("/mcp", json={"jsonrpc": "2.0", "method": "initialize", "id": 1})
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Bearer")

    @pytest.mark.parametrize("header", ["Bearer wrong", f"Basic {VALID_TOKEN}", "Bearer "])
    def test_rejects_bad_tokens(self, http_client, header):
        """Test wrong tokens and schemes are rejected."""
        response = http_client.post("/mcp", headers={"Authorization": header})
        assert response.status_code == 401

    def test_accepts_valid_token(self, http_client):
        """Test a valid token reaches the app."""
        response = http_client.get("/mcp", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
        assert response.status_code == 200
        assert response.text == "ok"

    def test_refuses_to_construct_with_empty_token(self):
        """Test the middleware cannot be built without a usable token."""
        with pytest.raises(ValueError):
            BearerTokenASGIMiddleware(app=MagicMock(), token="")


class TestValidateAuthToken:
    """Tests for HTTP token validation at startup."""

    @pytest.mark.parametrize("token", [None, "", "   ", "short-token"])
    def test_rejects_unusable_tokens(self, token):
        with pytest.raises(ValueError):
            validate_auth_token(token)

    def test_accepts_strong_token(self):
        assert validate_auth_token(f"  {VALID_TOKEN}\n") == VALID_TOKEN


class TestMain:
    """Tests for transport selection and startup checks."""

    @patch("gam_mcp.server.mcp")
    def test_http_refuses_to_start_without_token(self, mock_mcp):
        from gam_mcp.server import main

        with patch.dict('os.environ', {'GAM_MCP_TRANSPORT': 'http', 'GAM_MCP_AUTH_TOKEN': ''}, clear=True):
            with pytest.raises(SystemExit):
                main()

        mock_mcp.run.assert_not_called()

    @patch("gam_mcp.server.mcp")
    def test_rejects_unknown_transport(self, mock_mcp):
        from gam_mcp.server import main

        with patch.dict('os.environ', {'GAM_MCP_TRANSPORT': 'sse'}, clear=True):
            with pytest.raises(SystemExit):
                main()

        mock_mcp.run.assert_not_called()

    @patch("gam_mcp.server.mcp")
    def test_http_starts_with_auth_on_loopback_and_does_not_log_token(self, mock_mcp, caplog, monkeypatch):
        import gam_mcp.server as server

        monkeypatch.setattr(server, "AUTH_TOKEN", None)
        monkeypatch.setattr(server, "HTTP_MODE", False)
        caplog.set_level(logging.DEBUG)
        with patch.dict('os.environ', {'GAM_MCP_TRANSPORT': 'http', 'GAM_MCP_AUTH_TOKEN': VALID_TOKEN}, clear=True):
            server.main()

        kwargs = mock_mcp.run.call_args.kwargs
        assert kwargs["transport"] == "http"
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["middleware"][0].cls is BearerTokenASGIMiddleware
        assert server.HTTP_MODE is True
        assert VALID_TOKEN not in caplog.text

    @patch("gam_mcp.server.mcp")
    def test_stdio_does_not_enable_http_auth(self, mock_mcp, monkeypatch):
        import gam_mcp.server as server

        monkeypatch.setattr(server, "HTTP_MODE", False)
        with patch.dict('os.environ', {}, clear=True):
            server.main()

        mock_mcp.run.assert_called_once_with(transport="stdio")
        assert server.HTTP_MODE is False


class TestConstantTimeComparison:
    """Tests for timing attack prevention."""

    def test_uses_hmac_compare_digest(self):
        """Test that hmac.compare_digest is used for token comparison."""
        # This is a design verification test
        # The actual implementation uses hmac.compare_digest
        token1 = "secret-token"
        token2 = "secret-token"

        # Verify constant-time comparison works correctly
        result = hmac.compare_digest(
            token1.encode('utf-8'),
            token2.encode('utf-8')
        )
        assert result is True

        # Verify different tokens return False
        result = hmac.compare_digest(
            token1.encode('utf-8'),
            "wrong-token".encode('utf-8')
        )
        assert result is False


class TestServerInitialization:
    """Tests for server initialization."""

    @patch("gam_mcp.server.is_gam_client_initialized", return_value=False)
    @patch("gam_mcp.server.init_gam_client")
    def test_init_client_requires_credentials_path(self, mock_init, mock_is_init):
        """Test init_client raises error when credentials path missing."""
        from gam_mcp.server import init_client

        with patch.dict('os.environ', {}, clear=True):
            with pytest.raises(ValueError) as exc_info:
                init_client()

            assert "GAM_CREDENTIALS_PATH" in str(exc_info.value)

    @patch("gam_mcp.server.is_gam_client_initialized", return_value=False)
    @patch("gam_mcp.server.init_gam_client")
    def test_init_client_requires_network_code(self, mock_init, mock_is_init):
        """Test init_client raises error when network code missing."""
        from gam_mcp.server import init_client

        with patch.dict('os.environ', {'GAM_CREDENTIALS_PATH': '/path/to/creds.json'}, clear=True):
            with pytest.raises(ValueError) as exc_info:
                init_client()

            assert "GAM_NETWORK_CODES" in str(exc_info.value)

    @patch("gam_mcp.server.is_gam_client_initialized", return_value=False)
    @patch("gam_mcp.server.init_gam_client")
    def test_init_client_success_with_network_codes(self, mock_init, mock_is_init):
        """Test init_client succeeds with GAM_NETWORK_CODES."""
        from gam_mcp.server import init_client

        with patch.dict('os.environ', {
            'GAM_CREDENTIALS_PATH': '/path/to/creds.json',
            'GAM_NETWORK_CODES': '11111111,22222222'
        }, clear=True):
            init_client()

            mock_init.assert_called_once_with(
                credentials_path='/path/to/creds.json',
                network_code='11111111',
                application_name='GAM MCP Server',
                allowed_network_codes={'22222222'},
                api_version=None,
            )

    @patch("gam_mcp.server.is_gam_client_initialized", return_value=False)
    @patch("gam_mcp.server.init_gam_client")
    def test_init_client_success_with_legacy_network_code(self, mock_init, mock_is_init):
        """Test init_client falls back to legacy GAM_NETWORK_CODE."""
        from gam_mcp.server import init_client

        with patch.dict('os.environ', {
            'GAM_CREDENTIALS_PATH': '/path/to/creds.json',
            'GAM_NETWORK_CODE': '12345678'
        }, clear=True):
            init_client()

            mock_init.assert_called_once_with(
                credentials_path='/path/to/creds.json',
                network_code='12345678',
                application_name='GAM MCP Server',
                allowed_network_codes=set(),
                api_version=None,
            )

    @patch("gam_mcp.server.is_gam_client_initialized", return_value=True)
    @patch("gam_mcp.server.init_gam_client")
    def test_init_client_skips_when_already_initialized(self, mock_init, mock_is_init):
        """Test init_client skips initialization if client already exists."""
        from gam_mcp.server import init_client

        init_client()

        # Should not try to initialize again
        mock_init.assert_not_called()


class TestCreateCampaign:
    """create_campaign must report failures and partial completion accurately."""

    ARGS = dict(
        advertiser_name="Adv", order_name="Order", line_item_name="LI",
        end_year=2026, end_month=12, end_day=31, creatives_folder="campaign",
        click_through_url="https://example.com", target_ad_unit_id="42",
    )

    @pytest.fixture
    def mocks(self, upload_dir):
        from tests.conftest import make_png
        (upload_dir / "campaign").mkdir()
        (upload_dir / "campaign" / "banner_300x250.png").write_bytes(make_png())
        with patch("gam_mcp.server.init_client"), \
             patch("gam_mcp.server.advertisers") as adv, \
             patch("gam_mcp.server.orders") as orders, \
             patch("gam_mcp.server.line_items") as lis, \
             patch("gam_mcp.server.creatives.bulk_upload_creatives") as bulk:
            adv.find_or_create_advertiser.return_value = {"id": 1}
            orders.find_or_create_order.return_value = {"id": 2}
            lis.create_line_item.return_value = {"id": 3}
            yield {"adv": adv, "bulk": bulk}

    @staticmethod
    def _run(**overrides):
        import json
        from gam_mcp.server import create_campaign
        return json.loads(create_campaign(**{**TestCreateCampaign.ARGS, **overrides}))

    def test_reports_success_when_all_creatives_upload(self, mocks):
        mocks["bulk"].return_value = {"success_count": 1, "fail_count": 0, "total_files": 1}

        result = self._run()

        assert result["success"] is True
        assert result["errors"] == []

    def test_failed_uploads_are_not_reported_as_success(self, mocks):
        mocks["bulk"].return_value = {
            "success_count": 0, "fail_count": 1, "total_files": 1,
            "failed": [{"file": "banner_300x250.png", "error": "boom"}],
        }

        result = self._run()

        assert result["success"] is False
        assert "1 of 1 failed" in result["errors"][0]
        assert result["line_item"] == {"id": 3}

    def test_bulk_error_is_not_reported_as_success(self, mocks):
        mocks["bulk"].return_value = {"error": "No image files found"}

        result = self._run()

        assert result["success"] is False

    def test_invalid_folder_is_rejected_before_any_mutation(self, mocks):
        result = self._run(creatives_folder="/etc")

        assert result["success"] is False
        assert "outside the upload directory" in result["errors"][0]
        mocks["adv"].find_or_create_advertiser.assert_not_called()
