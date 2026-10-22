"""Tests for creative tools."""

import pytest
from unittest.mock import patch, MagicMock

from gam_mcp.tools import creatives
from tests.conftest import make_png


class TestExtractSizeFromFilename:
    """Tests for extract_size_from_filename function."""

    def test_extracts_size_from_standard_format(self):
        """Test extracts size from standard filename format."""
        width, height = creatives.extract_size_from_filename("banner_300x250.png")
        assert width == 300
        assert height == 250

    def test_extracts_size_from_middle_of_filename(self):
        """Test extracts size from middle of filename."""
        width, height = creatives.extract_size_from_filename("campaign_728x90_v2.jpg")
        assert width == 728
        assert height == 90

    def test_extracts_size_from_start_of_filename(self):
        """Test extracts size from start of filename."""
        width, height = creatives.extract_size_from_filename("1000x250_header.png")
        assert width == 1000
        assert height == 250

    def test_returns_none_when_no_size_found(self):
        """Test returns None when no size pattern found."""
        width, height = creatives.extract_size_from_filename("banner.png")
        assert width is None
        assert height is None

    def test_extracts_first_size_when_multiple_present(self):
        """Test extracts first size when multiple patterns present."""
        width, height = creatives.extract_size_from_filename("300x250_to_728x90.png")
        assert width == 300
        assert height == 250


class TestUploadCreative:
    """Tests for upload_creative function."""

    @staticmethod
    def _mock_service(mock_get_client, name="Creative - 300x250 - banner_300x250"):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_service = MagicMock()
        mock_service.createCreatives.return_value = [{"id": 123, "name": name}]
        mock_client.get_service.return_value = mock_service
        return mock_service

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_returns_error_when_file_not_found(self, mock_get_client, upload_dir):
        """Test returns error when file doesn't exist."""
        result = creatives.upload_creative(
            file_path="missing_300x250.png",
            advertiser_id=123,
            click_through_url="https://example.com"
        )

        assert "error" in result
        assert "not found" in result["error"]
        mock_get_client.assert_not_called()

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_returns_error_when_size_not_in_filename(self, mock_get_client, upload_dir):
        """Test returns error when size cannot be extracted from filename."""
        (upload_dir / "banner.png").write_bytes(make_png())

        result = creatives.upload_creative(
            file_path="banner.png",
            advertiser_id=123,
            click_through_url="https://example.com"
        )

        assert "error" in result
        assert "Could not extract size" in result["error"]

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_uploads_creative_successfully(self, mock_get_client, upload_dir):
        """Test successfully uploads a creative."""
        (upload_dir / "banner_300x250.png").write_bytes(make_png())
        mock_service = self._mock_service(mock_get_client)

        result = creatives.upload_creative(
            file_path="banner_300x250.png",
            advertiser_id=456,
            click_through_url="https://example.com"
        )

        assert result["id"] == 123
        assert result["size"] == "300x250"
        assert "uploaded successfully" in result["message"]
        mock_service.createCreatives.assert_called_once()

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_accepts_absolute_path_inside_upload_dir(self, mock_get_client, upload_dir):
        """Test absolute paths inside the upload directory are accepted."""
        path = upload_dir / "banner_300x250.png"
        path.write_bytes(make_png())
        self._mock_service(mock_get_client)

        result = creatives.upload_creative(
            file_path=str(path),
            advertiser_id=456,
            click_through_url="https://example.com"
        )

        assert result["id"] == 123

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_uses_size_override(self, mock_get_client, upload_dir):
        """Test uses size override when provided."""
        (upload_dir / "banner_970x250.png").write_bytes(make_png(970, 250))
        self._mock_service(mock_get_client, name="Creative - 1000x250 - banner_970x250")

        result = creatives.upload_creative(
            file_path="banner_970x250.png",
            advertiser_id=456,
            click_through_url="https://example.com",
            override_size_width=1000,
            override_size_height=250
        )

        assert result["size"] == "1000x250"
        assert result["original_size"] == "970x250"
        assert result["override_size"] is True

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_rejects_when_upload_dir_not_configured(self, mock_get_client, tmp_path, monkeypatch):
        """Test uploads are refused when GAM_UPLOAD_DIR is not set."""
        monkeypatch.delenv("GAM_UPLOAD_DIR", raising=False)
        path = tmp_path / "banner_300x250.png"
        path.write_bytes(make_png())

        result = creatives.upload_creative(
            file_path=str(path),
            advertiser_id=456,
            click_through_url="https://example.com"
        )

        assert "GAM_UPLOAD_DIR" in result["error"]
        mock_get_client.assert_not_called()

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_rejects_path_outside_upload_dir(self, mock_get_client, upload_dir, tmp_path):
        """Test absolute and ../ paths outside the upload directory are refused."""
        outside = tmp_path / "secret_300x250.png"
        outside.write_bytes(make_png())

        for file_path in (str(outside), "../secret_300x250.png"):
            result = creatives.upload_creative(
                file_path=file_path,
                advertiser_id=456,
                click_through_url="https://example.com"
            )
            assert "outside the upload directory" in result["error"]
        mock_get_client.assert_not_called()

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_rejects_symlink_escaping_upload_dir(self, mock_get_client, upload_dir, tmp_path):
        """Test a symlink inside the upload directory pointing outside is refused."""
        secret = tmp_path / "credentials.json"
        secret.write_text('{"private_key": "not-real"}')
        (upload_dir / "banner_300x250.png").symlink_to(secret)

        result = creatives.upload_creative(
            file_path="banner_300x250.png",
            advertiser_id=456,
            click_through_url="https://example.com"
        )

        assert "outside the upload directory" in result["error"]
        mock_get_client.assert_not_called()

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_rejects_non_image_content(self, mock_get_client, upload_dir):
        """Test files that are not real images are refused."""
        (upload_dir / "banner_300x250.png").write_text("PRIVATE KEY")

        result = creatives.upload_creative(
            file_path="banner_300x250.png",
            advertiser_id=456,
            click_through_url="https://example.com"
        )

        assert "not a PNG, JPEG or GIF" in result["error"]
        mock_get_client.assert_not_called()

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_rejects_directory_and_oversized_file(self, mock_get_client, upload_dir):
        """Test directories and files over the size limit are refused."""
        (upload_dir / "dir_300x250.png").mkdir()
        result = creatives.upload_creative(
            file_path="dir_300x250.png",
            advertiser_id=456,
            click_through_url="https://example.com"
        )
        assert "Not a regular file" in result["error"]

        big = upload_dir / "big_300x250.png"
        big.write_bytes(make_png() + b"\x00" * creatives.MAX_UPLOAD_BYTES)
        result = creatives.upload_creative(
            file_path="big_300x250.png",
            advertiser_id=456,
            click_through_url="https://example.com"
        )
        assert "limit" in result["error"]
        mock_get_client.assert_not_called()


class TestAssociateCreativeWithLineItem:
    """Tests for associate_creative_with_line_item function."""

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_associates_creative_successfully(self, mock_get_client):
        """Test successfully associates creative with line item."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_service = MagicMock()
        mock_service.createLineItemCreativeAssociations.return_value = [{}]
        mock_client.get_service.return_value = mock_service

        result = creatives.associate_creative_with_line_item(
            creative_id=123,
            line_item_id=456
        )

        assert result["creative_id"] == 123
        assert result["line_item_id"] == 456
        assert "associated" in result["message"]

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_uses_size_override(self, mock_get_client):
        """Test uses size override when provided."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_service = MagicMock()
        mock_service.createLineItemCreativeAssociations.return_value = [{}]
        mock_client.get_service.return_value = mock_service

        result = creatives.associate_creative_with_line_item(
            creative_id=123,
            line_item_id=456,
            size_override_width=1000,
            size_override_height=250
        )

        assert result["size_override"] == "1000x250"

        # Verify the LICA was created with size override
        call_args = mock_service.createLineItemCreativeAssociations.call_args[0][0][0]
        assert call_args["sizes"][0]["width"] == 1000
        assert call_args["sizes"][0]["height"] == 250

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_returns_error_on_failure(self, mock_get_client):
        """Test returns error when association fails."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_service = MagicMock()
        mock_service.createLineItemCreativeAssociations.return_value = None
        mock_client.get_service.return_value = mock_service

        result = creatives.associate_creative_with_line_item(
            creative_id=123,
            line_item_id=456
        )

        assert "error" in result


class TestUploadAndAssociateCreative:
    """Tests for upload_and_associate_creative function."""

    @patch("gam_mcp.tools.creatives.associate_creative_with_line_item")
    @patch("gam_mcp.tools.creatives.upload_creative")
    def test_uploads_and_associates_successfully(self, mock_upload, mock_associate):
        """Test successfully uploads and associates creative."""
        mock_upload.return_value = {
            "id": 123,
            "name": "Creative - 300x250",
            "size": "300x250"
        }
        mock_associate.return_value = {
            "creative_id": 123,
            "line_item_id": 456,
            "message": "associated"
        }

        result = creatives.upload_and_associate_creative(
            file_path="/path/to/banner_300x250.png",
            advertiser_id=789,
            line_item_id=456,
            click_through_url="https://example.com"
        )

        assert result["creative_id"] == 123
        assert result["line_item_id"] == 456
        assert "uploaded and associated" in result["message"]

    @patch("gam_mcp.tools.creatives.upload_creative")
    def test_returns_upload_error(self, mock_upload):
        """Test returns error when upload fails."""
        mock_upload.return_value = {"error": "Upload failed"}

        result = creatives.upload_and_associate_creative(
            file_path="/path/to/banner_300x250.png",
            advertiser_id=789,
            line_item_id=456,
            click_through_url="https://example.com"
        )

        assert "error" in result

    @patch("gam_mcp.tools.creatives.associate_creative_with_line_item")
    @patch("gam_mcp.tools.creatives.upload_creative")
    def test_returns_association_error(self, mock_upload, mock_associate):
        """Test returns error when association fails."""
        mock_upload.return_value = {
            "id": 123,
            "name": "Creative",
            "size": "300x250"
        }
        mock_associate.return_value = {"error": "Association failed"}

        result = creatives.upload_and_associate_creative(
            file_path="/path/to/banner_300x250.png",
            advertiser_id=789,
            line_item_id=456,
            click_through_url="https://example.com"
        )

        assert "association_error" in result
        # Must look like a failure to callers that check for "error"
        assert "error" in result
        assert result["creative_id"] == 123


class TestBulkUploadCreatives:
    """Tests for bulk_upload_creatives function."""

    def test_returns_error_when_folder_not_found(self, upload_dir):
        """Test returns error when folder doesn't exist."""
        result = creatives.bulk_upload_creatives(
            folder_path="missing",
            advertiser_id=123,
            line_item_id=456,
            click_through_url="https://example.com"
        )

        assert "error" in result
        assert "not found" in result["error"]

    def test_returns_error_when_folder_outside_upload_dir(self, upload_dir, tmp_path):
        """Test refuses folders outside the upload directory."""
        result = creatives.bulk_upload_creatives(
            folder_path=str(tmp_path),
            advertiser_id=123,
            line_item_id=456,
            click_through_url="https://example.com"
        )

        assert "outside the upload directory" in result["error"]

    def test_returns_error_when_no_images_found(self, upload_dir):
        """Test returns error when folder has no images."""
        (upload_dir / "empty").mkdir()

        result = creatives.bulk_upload_creatives(
            folder_path="empty",
            advertiser_id=123,
            line_item_id=456,
            click_through_url="https://example.com"
        )

        assert "error" in result
        assert "No image files" in result["error"]

    @patch("gam_mcp.tools.creatives.upload_and_associate_creative")
    def test_uploads_all_images_successfully(self, mock_upload_assoc, upload_dir):
        """Test uploads all images from folder."""
        (upload_dir / "banner_300x250.png").write_bytes(make_png())
        (upload_dir / "banner_728x90.PNG").write_bytes(make_png(728, 90))
        (upload_dir / "notes.txt").write_text("ignored")

        mock_upload_assoc.return_value = {
            "creative_id": 123,
            "size": "300x250",
            "message": "success"
        }

        result = creatives.bulk_upload_creatives(
            folder_path=".",
            advertiser_id=123,
            line_item_id=456,
            click_through_url="https://example.com"
        )

        assert result["success_count"] == 2
        assert result["fail_count"] == 0
        assert result["success"] is True

    @patch("gam_mcp.tools.creatives.upload_and_associate_creative")
    def test_reports_association_failure_as_failed(self, mock_upload_assoc, upload_dir):
        """Test an upload whose association failed is reported, not treated as success."""
        (upload_dir / "banner_300x250.png").write_bytes(make_png())
        mock_upload_assoc.return_value = {
            "error": "Creative 99 uploaded but association failed: boom",
            "creative_id": 99,
            "association_error": "boom",
        }

        result = creatives.bulk_upload_creatives(
            folder_path=".",
            advertiser_id=123,
            line_item_id=456,
            click_through_url="https://example.com"
        )

        assert result["success"] is False
        assert result["fail_count"] == 1
        assert result["failed"][0]["creative_id"] == 99

    @patch("gam_mcp.tools.creatives.upload_and_associate_creative")
    def test_api_exception_is_reported_per_file(self, mock_upload_assoc, upload_dir):
        """Test an API exception for one file does not abort the batch."""
        (upload_dir / "a_300x250.png").write_bytes(make_png())
        (upload_dir / "b_728x90.png").write_bytes(make_png(728, 90))
        mock_upload_assoc.side_effect = [
            RuntimeError("API fault"),
            {"creative_id": 2, "size": "728x90"},
        ]

        result = creatives.bulk_upload_creatives(
            folder_path=".",
            advertiser_id=123,
            line_item_id=456,
            click_through_url="https://example.com"
        )

        assert result["success_count"] == 1
        assert result["failed"] == [{"file": "a_300x250.png", "error": "API fault"}]
        assert result["success"] is False


class TestGetCreative:
    """Tests for get_creative function."""

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_returns_error_when_not_found(self, mock_get_client):
        """Test returns error when creative doesn't exist."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_service = MagicMock()
        mock_service.getCreativesByStatement.return_value = {}
        mock_client.get_service.return_value = mock_service
        mock_client.create_statement.return_value = MagicMock()

        result = creatives.get_creative(creative_id=999)

        assert "error" in result

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_returns_creative_details(self, mock_get_client):
        """Test returns creative details successfully."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_service = MagicMock()
        mock_service.getCreativesByStatement.return_value = {
            "results": [{
                "id": 123,
                "name": "Banner Creative",
                "advertiserId": 456,
                "size": {"width": 300, "height": 250},
                "destinationUrl": "https://example.com"
            }]
        }
        mock_client.get_service.return_value = mock_service
        mock_client.create_statement.return_value = MagicMock()

        result = creatives.get_creative(creative_id=123)

        assert result["id"] == 123
        assert result["name"] == "Banner Creative"
        assert result["size"] == "300x250"


class TestListCreativesByAdvertiser:
    """Tests for list_creatives_by_advertiser function."""

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_returns_empty_when_no_creatives(self, mock_get_client):
        """Test returns empty list when no creatives exist."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_service = MagicMock()
        mock_service.getCreativesByStatement.return_value = {}
        mock_client.get_service.return_value = mock_service
        mock_client.create_statement.return_value = MagicMock()

        result = creatives.list_creatives_by_advertiser(advertiser_id=123)

        assert result["creatives"] == []
        assert result["total"] == 0

    @patch("gam_mcp.tools.creatives.get_gam_client")
    def test_returns_creatives_for_advertiser(self, mock_get_client):
        """Test returns all creatives for advertiser."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_service = MagicMock()
        mock_service.getCreativesByStatement.return_value = {
            "results": [
                {"id": 1, "name": "Creative 1", "size": {"width": 300, "height": 250}},
                {"id": 2, "name": "Creative 2", "size": {"width": 728, "height": 90}}
            ]
        }
        mock_client.get_service.return_value = mock_service
        mock_client.create_statement.return_value = MagicMock()

        result = creatives.list_creatives_by_advertiser(advertiser_id=123)

        assert result["total"] == 2
        assert result["creatives"][0]["size"] == "300x250"
        assert result["creatives"][1]["size"] == "728x90"
