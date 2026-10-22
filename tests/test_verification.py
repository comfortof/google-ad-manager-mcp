"""Tests for verification tools."""

import pytest
from unittest.mock import patch, MagicMock

from gam_mcp.tools import verification


class TestVerifyLineItemSetup:
    """Tests for verify_line_item_setup function."""

    @patch("gam_mcp.tools.verification.get_gam_client")
    def test_returns_error_when_not_found(self, mock_get_client):
        """Test returns error when line item doesn't exist."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_service = MagicMock()
        mock_service.getLineItemsByStatement.return_value = {}
        mock_client.get_service.return_value = mock_service
        mock_client.create_statement.return_value = MagicMock()

        result = verification.verify_line_item_setup(line_item_id=999)

        assert "error" in result

    @patch("gam_mcp.tools.verification.get_gam_client")
    def test_returns_ok_status_when_no_issues(self, mock_get_client):
        """Test returns OK status when no issues found."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_line_item_service = MagicMock()
        mock_lica_service = MagicMock()
        mock_creative_service = MagicMock()

        mock_line_item_service.getLineItemsByStatement.return_value = {
            "results": [{
                "id": 123,
                "name": "Test Line Item",
                "status": "READY",
                "orderId": 456,
                "creativePlaceholders": [
                    {"size": {"width": 300, "height": 250}}
                ]
            }]
        }

        mock_lica_service.getLineItemCreativeAssociationsByStatement.return_value = {
            "results": [{
                "creativeId": 789,
                "lineItemId": 123,
                "status": "ACTIVE"
            }]
        }

        mock_creative_service.getCreativesByStatement.return_value = {
            "results": [{
                "id": 789,
                "name": "Banner",
                "size": {"width": 300, "height": 250}
            }]
        }

        def get_service(name):
            if name == "LineItemService":
                return mock_line_item_service
            elif name == "LineItemCreativeAssociationService":
                return mock_lica_service
            return mock_creative_service

        mock_client.get_service.side_effect = get_service
        mock_client.create_statement.return_value = MagicMock()

        result = verification.verify_line_item_setup(line_item_id=123)

        assert result["status"] == "OK"
        assert len(result["issues"]) == 0

    @patch("gam_mcp.tools.verification.get_gam_client")
    def test_detects_size_mismatch(self, mock_get_client):
        """Test detects when creative size doesn't match placeholder."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_line_item_service = MagicMock()
        mock_lica_service = MagicMock()
        mock_creative_service = MagicMock()

        # Line item expects 300x250
        mock_line_item_service.getLineItemsByStatement.return_value = {
            "results": [{
                "id": 123,
                "name": "Test Line Item",
                "status": "READY",
                "orderId": 456,
                "creativePlaceholders": [
                    {"size": {"width": 300, "height": 250}}
                ]
            }]
        }

        mock_lica_service.getLineItemCreativeAssociationsByStatement.return_value = {
            "results": [{
                "creativeId": 789,
                "lineItemId": 123,
                "status": "ACTIVE"
            }]
        }

        # Creative is 728x90 - mismatch!
        mock_creative_service.getCreativesByStatement.return_value = {
            "results": [{
                "id": 789,
                "name": "Banner",
                "size": {"width": 728, "height": 90}
            }]
        }

        def get_service(name):
            if name == "LineItemService":
                return mock_line_item_service
            elif name == "LineItemCreativeAssociationService":
                return mock_lica_service
            return mock_creative_service

        mock_client.get_service.side_effect = get_service
        mock_client.create_statement.return_value = MagicMock()

        result = verification.verify_line_item_setup(line_item_id=123)

        assert result["status"] == "ISSUES_FOUND"
        assert len(result["issues"]) == 1
        assert result["issues"][0]["type"] == "SIZE_MISMATCH"

    @patch("gam_mcp.tools.verification.get_gam_client")
    def test_detects_no_creatives(self, mock_get_client):
        """Test detects when line item has no creative associations."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_line_item_service = MagicMock()
        mock_lica_service = MagicMock()
        mock_creative_service = MagicMock()

        mock_line_item_service.getLineItemsByStatement.return_value = {
            "results": [{
                "id": 123,
                "name": "Test Line Item",
                "status": "READY",
                "orderId": 456,
                "creativePlaceholders": [
                    {"size": {"width": 300, "height": 250}}
                ]
            }]
        }

        # No creative associations
        mock_lica_service.getLineItemCreativeAssociationsByStatement.return_value = {}

        def get_service(name):
            if name == "LineItemService":
                return mock_line_item_service
            elif name == "LineItemCreativeAssociationService":
                return mock_lica_service
            return mock_creative_service

        mock_client.get_service.side_effect = get_service
        mock_client.create_statement.return_value = MagicMock()

        result = verification.verify_line_item_setup(line_item_id=123)

        assert result["status"] == "NO_CREATIVES"
        assert any(i["type"] == "NO_CREATIVES" for i in result["issues"])

    @patch("gam_mcp.tools.verification.get_gam_client")
    def test_accepts_size_override(self, mock_get_client):
        """Test accepts creative with size override matching placeholder."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_line_item_service = MagicMock()
        mock_lica_service = MagicMock()
        mock_creative_service = MagicMock()

        # Line item expects 1000x250
        mock_line_item_service.getLineItemsByStatement.return_value = {
            "results": [{
                "id": 123,
                "name": "Test Line Item",
                "status": "READY",
                "orderId": 456,
                "creativePlaceholders": [
                    {"size": {"width": 1000, "height": 250}}
                ]
            }]
        }

        # LICA has size override to 1000x250
        mock_lica_service.getLineItemCreativeAssociationsByStatement.return_value = {
            "results": [{
                "creativeId": 789,
                "lineItemId": 123,
                "status": "ACTIVE",
                "sizes": [{"width": 1000, "height": 250}]
            }]
        }

        # Creative is 970x250 but with size override
        mock_creative_service.getCreativesByStatement.return_value = {
            "results": [{
                "id": 789,
                "name": "Banner",
                "size": {"width": 970, "height": 250}
            }]
        }

        def get_service(name):
            if name == "LineItemService":
                return mock_line_item_service
            elif name == "LineItemCreativeAssociationService":
                return mock_lica_service
            return mock_creative_service

        mock_client.get_service.side_effect = get_service
        mock_client.create_statement.return_value = MagicMock()

        result = verification.verify_line_item_setup(line_item_id=123)

        # Should be OK because size override matches placeholder
        assert result["status"] == "OK"
        assert len(result["issues"]) == 0


class TestCheckLineItemDeliveryStatus:
    """Tests for check_line_item_delivery_status function."""

    @patch("gam_mcp.tools.verification.get_gam_client")
    def test_returns_error_when_not_found(self, mock_get_client):
        """Test returns error when line item doesn't exist."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_service = MagicMock()
        mock_service.getLineItemsByStatement.return_value = {}
        mock_client.get_service.return_value = mock_service
        mock_client.create_statement.return_value = MagicMock()

        result = verification.check_line_item_delivery_status(line_item_id=999)

        assert "error" in result

    @patch("gam_mcp.tools.verification.get_gam_client")
    def test_returns_delivery_status(self, mock_get_client):
        """Test returns delivery status with progress."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_service = MagicMock()
        mock_service.getLineItemsByStatement.return_value = {
            "results": [{
                "id": 123,
                "name": "Test Line Item",
                "status": "DELIVERING",
                "lineItemType": "STANDARD",
                "stats": {
                    "impressionsDelivered": 50000,
                    "clicksDelivered": 250
                },
                "primaryGoal": {
                    "goalType": "LIFETIME",
                    "unitType": "IMPRESSIONS",
                    "units": 100000
                },
                "deliveryRateType": "EVENLY"
            }]
        }
        mock_client.get_service.return_value = mock_service
        mock_client.create_statement.return_value = MagicMock()

        result = verification.check_line_item_delivery_status(line_item_id=123)

        assert result["status"] == "DELIVERING"
        assert result["delivery"]["impressions_delivered"] == 50000
        assert result["delivery"]["goal_units"] == 100000
        assert result["delivery"]["progress_percent"] == 50.0

    @patch("gam_mcp.tools.verification.get_gam_client")
    def test_handles_zero_goal(self, mock_get_client):
        """Test handles zero goal without division error."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_service = MagicMock()
        mock_service.getLineItemsByStatement.return_value = {
            "results": [{
                "id": 123,
                "name": "Test Line Item",
                "status": "READY",
                "lineItemType": "SPONSORSHIP",
                "stats": {"impressionsDelivered": 0, "clicksDelivered": 0},
                "primaryGoal": {"units": 0}
            }]
        }
        mock_client.get_service.return_value = mock_service
        mock_client.create_statement.return_value = MagicMock()

        result = verification.check_line_item_delivery_status(line_item_id=123)

        assert result["delivery"]["progress_percent"] == 0


class TestVerifyOrderSetup:
    """Tests for verify_order_setup function."""

    @patch("gam_mcp.tools.verification.get_gam_client")
    def test_returns_error_when_not_found(self, mock_get_client):
        """Test returns error when order doesn't exist."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_service = MagicMock()
        mock_service.getOrdersByStatement.return_value = {}
        mock_client.get_service.return_value = mock_service
        mock_client.create_statement.return_value = MagicMock()

        result = verification.verify_order_setup(order_id=999)

        assert "error" in result

    @patch("gam_mcp.tools.verification.get_gam_client")
    @patch("gam_mcp.tools.verification.verify_line_item_setup")
    def test_verifies_all_line_items(self, mock_verify_li, mock_get_client):
        """Test verifies all line items in order."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_order_service = MagicMock()
        mock_line_item_service = MagicMock()

        mock_order_service.getOrdersByStatement.return_value = {
            "results": [{
                "id": 123,
                "name": "Test Order",
                "status": "APPROVED",
                "advertiserId": 456
            }]
        }

        mock_line_item_service.getLineItemsByStatement.return_value = {
            "results": [
                {"id": 1, "name": "LI 1"},
                {"id": 2, "name": "LI 2"}
            ]
        }

        def get_service(name):
            if name == "OrderService":
                return mock_order_service
            return mock_line_item_service

        mock_client.get_service.side_effect = get_service
        mock_client.create_statement.return_value = MagicMock()

        mock_verify_li.return_value = {
            "line_item": {"name": "Test", "status": "READY"},
            "summary": {"creative_count": 2, "issue_count": 0},
            "issues": []
        }

        result = verification.verify_order_setup(order_id=123)

        assert result["order_id"] == 123
        assert len(result["line_items"]) == 2
        assert mock_verify_li.call_count == 2

    @patch("gam_mcp.tools.verification.get_gam_client")
    @patch("gam_mcp.tools.verification.verify_line_item_setup")
    def test_aggregates_issues_from_line_items(self, mock_verify_li, mock_get_client):
        """Test aggregates issues from all line items."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_order_service = MagicMock()
        mock_line_item_service = MagicMock()

        mock_order_service.getOrdersByStatement.return_value = {
            "results": [{"id": 123, "name": "Test Order", "status": "APPROVED"}]
        }

        mock_line_item_service.getLineItemsByStatement.return_value = {
            "results": [{"id": 1, "name": "LI 1"}]
        }

        def get_service(name):
            if name == "OrderService":
                return mock_order_service
            return mock_line_item_service

        mock_client.get_service.side_effect = get_service
        mock_client.create_statement.return_value = MagicMock()

        mock_verify_li.return_value = {
            "line_item": {"name": "Test", "status": "READY"},
            "summary": {"creative_count": 0, "issue_count": 1},
            "issues": [{"type": "NO_CREATIVES", "message": "No creatives"}]
        }

        result = verification.verify_order_setup(order_id=123)

        assert result["overall_status"] == "ISSUES_FOUND"
        assert result["summary"]["total_issues"] == 1

    @patch("gam_mcp.tools.verification.get_gam_client")
    @patch("gam_mcp.tools.verification.verify_line_item_setup")
    def test_handles_line_item_verification_error(self, mock_verify_li, mock_get_client):
        """Test handles error from line item verification."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_order_service = MagicMock()
        mock_line_item_service = MagicMock()

        mock_order_service.getOrdersByStatement.return_value = {
            "results": [{"id": 123, "name": "Test Order", "status": "APPROVED"}]
        }

        mock_line_item_service.getLineItemsByStatement.return_value = {
            "results": [{"id": 1, "name": "LI 1"}]
        }

        def get_service(name):
            if name == "OrderService":
                return mock_order_service
            return mock_line_item_service

        mock_client.get_service.side_effect = get_service
        mock_client.create_statement.return_value = MagicMock()

        mock_verify_li.return_value = {"error": "Verification failed"}

        result = verification.verify_order_setup(order_id=123)

        assert "error" in result["line_items"][0]


class TestVerificationReliability:
    """Pagination, error status and Zeep-compatibility regressions."""

    @staticmethod
    def _order_client(mock_get_client, line_item_pages):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        order_service = MagicMock()
        order_service.getOrdersByStatement.return_value = {
            "results": [{"id": 123, "name": "Order", "status": "APPROVED"}]
        }
        li_service = MagicMock()
        li_service.getLineItemsByStatement.side_effect = line_item_pages
        mock_client.get_service.side_effect = (
            lambda name: order_service if name == "OrderService" else li_service
        )
        mock_client.create_statement.return_value = MagicMock()
        return li_service

    @patch("gam_mcp.tools.verification.get_gam_client")
    @patch("gam_mcp.tools.verification.verify_line_item_setup")
    def test_verifies_line_items_on_every_page(self, mock_verify_li, mock_get_client):
        page_size = 500
        first = [{"id": i, "name": f"LI {i}"} for i in range(page_size)]
        second = [{"id": page_size, "name": "Broken"}]
        li_service = self._order_client(mock_get_client, [
            {"results": first, "totalResultSetSize": page_size + 1},
            {"results": second, "totalResultSetSize": page_size + 1},
        ])

        def verify(li_id, network_code=None):
            issues = [{"type": "SIZE_MISMATCH"}] if li_id == page_size else []
            return {
                "line_item": {"name": "x", "status": "READY"},
                "summary": {"creative_count": 1, "issue_count": len(issues)},
                "issues": issues,
            }
        mock_verify_li.side_effect = verify

        result = verification.verify_order_setup(order_id=123)

        assert li_service.getLineItemsByStatement.call_count == 2
        assert result["summary"]["line_item_count"] == page_size + 1
        assert result["overall_status"] == "ISSUES_FOUND"

    @patch("gam_mcp.tools.verification.get_gam_client")
    @patch("gam_mcp.tools.verification.verify_line_item_setup")
    def test_child_errors_make_status_incomplete(self, mock_verify_li, mock_get_client):
        self._order_client(mock_get_client, [{"results": [{"id": 1, "name": "LI"}, {"id": 2, "name": "LI 2"}]}])
        mock_verify_li.side_effect = [{"error": "not found"}, RuntimeError("API fault")]

        result = verification.verify_order_setup(order_id=123)

        assert result["overall_status"] == "INCOMPLETE"
        assert result["summary"]["unverified_line_items"] == 2
        assert "API fault" in result["errors"][1]["error"]

    @patch("gam_mcp.tools.verification.get_gam_client")
    def test_line_item_setup_works_with_attribute_only_objects(self, mock_get_client, mock_zeep_object):
        """Objects without .get() (like zeep) must not crash verification."""
        Z = mock_zeep_object
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        li_service, lica_service, creative_service = MagicMock(), MagicMock(), MagicMock()
        li_service.getLineItemsByStatement.return_value = {"results": [Z({
            "id": 1, "name": "LI", "status": "READY", "lineItemType": "STANDARD", "orderId": 9,
            "creativePlaceholders": [Z({"size": Z({"width": 300, "height": 250})})],
        })]}
        lica_service.getLineItemCreativeAssociationsByStatement.return_value = {"results": [
            Z({"creativeId": 5, "status": "ACTIVE", "sizes": None})
        ]}
        creative_service.getCreativesByStatement.return_value = {"results": [
            Z({"name": "C", "size": Z({"width": 300, "height": 250})})
        ]}
        mock_client.get_service.side_effect = lambda name: {
            "LineItemService": li_service,
            "LineItemCreativeAssociationService": lica_service,
            "CreativeService": creative_service,
        }[name]

        result = verification.verify_line_item_setup(line_item_id=1)

        assert result["status"] == "OK"
        assert result["creative_associations"][0]["creative_size"] == "300x250"

    @patch("gam_mcp.tools.verification.get_gam_client")
    def test_delivery_status_reports_delivery_indicator(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_service = MagicMock()
        mock_service.getLineItemsByStatement.return_value = {"results": [{
            "id": 1, "name": "LI", "isSetTopBoxEnabled": True,
            "deliveryIndicator": {"expectedDeliveryPercentage": 75.0, "actualDeliveryPercentage": 2.0},
        }]}
        mock_client.get_service.return_value = mock_service

        result = verification.check_line_item_delivery_status(line_item_id=1)

        assert "is_set_to_deliver" not in result
        assert result["delivery_indicator"] == {
            "expected_delivery_percentage": 75.0,
            "actual_delivery_percentage": 2.0,
        }
