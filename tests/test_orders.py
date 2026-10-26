"""Tests for order tools."""

import pytest
from unittest.mock import patch, MagicMock

from gam_mcp.tools import orders


class TestListDeliveringOrders:
    """Tests for list_delivering_orders function."""

    @patch("gam_mcp.tools.orders.get_gam_client")
    def test_returns_empty_when_no_delivering_line_items(self, mock_get_client):
        """Test returns empty list when no delivering line items exist."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_line_item_service = MagicMock()
        mock_line_item_service.getLineItemsByStatement.return_value = {}
        mock_client.get_service.return_value = mock_line_item_service

        result = orders.list_delivering_orders()

        assert result["orders"] == []
        assert "No delivering line items found" in result["message"]

    @staticmethod
    def _setup(mock_get_client, line_item_pages, order_ids):
        """Line item pages in order; every requested order ID that is in order_ids is found."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        services = {"LineItemService": MagicMock(), "OrderService": MagicMock()}
        mock_client.get_service.side_effect = services.__getitem__
        services["LineItemService"].getLineItemsByStatement.side_effect = line_item_pages

        where_clauses = []

        def where(clause):
            where_clauses.append(clause)
            return mock_client.create_statement.return_value

        mock_client.create_statement.return_value.Where.side_effect = where
        mock_client.create_statement.return_value.WithBindVariable.return_value = (
            mock_client.create_statement.return_value
        )

        def get_orders(_statement):
            requested = [int(i) for i in where_clauses[-1][len("id IN ("):-1].split(", ")]
            return {"results": [
                {"id": i, "name": f"Order {i}", "status": "APPROVED"} for i in requested if i in order_ids
            ]}

        services["OrderService"].getOrdersByStatement.side_effect = get_orders
        return services, where_clauses

    @patch("gam_mcp.tools.orders.get_gam_client")
    def test_summary_by_default(self, mock_get_client):
        """Orders carry line item counts and delivery totals, not the line items."""
        line_items = [
            {"id": 1, "orderId": 100, "stats": {"impressionsDelivered": 10, "clicksDelivered": 1}},
            {"id": 2, "orderId": 100, "stats": {"impressionsDelivered": 5, "clicksDelivered": 0}},
            {"id": 3, "orderId": 200},
        ]
        self._setup(mock_get_client, [{"results": line_items}], {100, 200})

        result = orders.list_delivering_orders()

        assert result["total_orders"] == 2
        assert result["total_line_items"] == 3
        first = result["orders"][0]
        assert (first["id"], first["delivering_line_items"]) == (100, 2)
        assert (first["impressions_delivered"], first["clicks_delivered"]) == (15, 1)
        assert "line_items" not in first
        assert "include_line_items" in result["message"]

    @patch("gam_mcp.tools.orders.get_gam_client")
    def test_include_line_items(self, mock_get_client):
        """Line item details are returned when asked for."""
        line_items = [
            {"id": 1, "orderId": 100, "name": "LI1", "status": "DELIVERING"},
            {"id": 2, "orderId": 100, "name": "LI2", "status": "DELIVERING"},
        ]
        self._setup(mock_get_client, [{"results": line_items}], {100})

        result = orders.list_delivering_orders(include_line_items=True)

        assert [li["name"] for li in result["orders"][0]["line_items"]] == ["LI1", "LI2"]
        assert "message" not in result

    @patch("gam_mcp.tools.orders.get_gam_client")
    def test_orders_fetched_in_batches(self, mock_get_client):
        """Orders are looked up a batch at a time, not one call per order."""
        line_items = [{"id": i, "orderId": 1000 + i} for i in range(450)]
        services, where_clauses = self._setup(mock_get_client, [{"results": line_items}], set(range(1000, 1449)))

        result = orders.list_delivering_orders()

        assert services["OrderService"].getOrdersByStatement.call_count == 2
        assert [c.count(",") + 1 for c in where_clauses if c.startswith("id IN")] == [400, 50]
        # Line items whose order is not found are left out
        assert result["total_orders"] == 449

    @patch("gam_mcp.tools.orders.get_gam_client")
    def test_reads_every_page_of_line_items(self, mock_get_client):
        """Delivering line items past GAM's 500 per page are not dropped."""
        pages = [
            {"results": [{"id": i, "orderId": 100} for i in range(500)], "totalResultSetSize": 540},
            {"results": [{"id": 500 + i, "orderId": 200} for i in range(40)], "totalResultSetSize": 540},
        ]
        services, _ = self._setup(mock_get_client, pages, {100, 200})

        result = orders.list_delivering_orders()

        assert services["LineItemService"].getLineItemsByStatement.call_count == 2
        assert result["total_orders"] == 2
        assert result["total_line_items"] == 540


class TestGetOrder:
    """Tests for get_order function."""

    @patch("gam_mcp.tools.orders.get_gam_client")
    def test_requires_id_or_name(self, mock_get_client):
        """Test returns error when neither ID nor name provided."""
        result = orders.get_order()

        assert "error" in result
        assert "Either order_id or order_name must be provided" in result["error"]

    @patch("gam_mcp.tools.orders.get_gam_client")
    def test_returns_error_when_order_not_found(self, mock_get_client):
        """Test returns error when order doesn't exist."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_order_service = MagicMock()
        mock_order_service.getOrdersByStatement.return_value = {}
        mock_client.get_service.return_value = mock_order_service
        mock_client.create_statement.return_value = MagicMock()

        result = orders.get_order(order_id=999)

        assert "error" in result
        assert "not found" in result["error"]

    @patch("gam_mcp.tools.orders.get_gam_client")
    def test_fetches_order_by_id(self, mock_get_client):
        """Test successfully fetches order by ID."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_order_service = MagicMock()
        mock_line_item_service = MagicMock()

        mock_order_service.getOrdersByStatement.return_value = {
            "results": [{
                "id": 123,
                "name": "Test Order",
                "status": "APPROVED",
                "advertiserId": 456,
                "traffickerId": 789
            }]
        }
        mock_line_item_service.getLineItemsByStatement.return_value = {
            "results": [{"id": 1, "name": "LI1", "status": "READY", "lineItemType": "STANDARD"}]
        }

        def get_service(name):
            if name == "OrderService":
                return mock_order_service
            return mock_line_item_service

        mock_client.get_service.side_effect = get_service
        mock_client.create_statement.return_value = MagicMock()

        result = orders.get_order(order_id=123)

        assert result["id"] == 123
        assert result["name"] == "Test Order"
        assert result["total_line_items"] == 1

    @patch("gam_mcp.tools.orders.get_gam_client")
    def test_fetches_order_by_name(self, mock_get_client):
        """Test successfully fetches order by name."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_order_service = MagicMock()
        mock_line_item_service = MagicMock()

        mock_order_service.getOrdersByStatement.return_value = {
            "results": [{"id": 123, "name": "Test Order", "status": "APPROVED"}]
        }
        mock_line_item_service.getLineItemsByStatement.return_value = {"results": []}

        def get_service(name):
            if name == "OrderService":
                return mock_order_service
            return mock_line_item_service

        mock_client.get_service.side_effect = get_service
        mock_client.create_statement.return_value = MagicMock()

        result = orders.get_order(order_name="Test Order")

        assert result["id"] == 123
        assert result["name"] == "Test Order"


class TestCreateOrder:
    """Tests for create_order function."""

    @patch("gam_mcp.tools.orders.get_gam_client")
    def test_creates_order_successfully(self, mock_get_client):
        """Test successfully creates an order."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_order_service = MagicMock()
        mock_order_service.createOrders.return_value = [{
            "id": 123,
            "name": "New Order",
            "status": "DRAFT",
            "advertiserId": 456,
            "traffickerId": 789
        }]

        mock_client.get_service.return_value = mock_order_service
        mock_client.create_statement.return_value = MagicMock()

        result = orders.create_order(
            order_name="New Order",
            advertiser_id=456,
            trafficker_id=789
        )

        assert result["id"] == 123
        assert result["name"] == "New Order"
        assert "created successfully" in result["message"]

    @patch("gam_mcp.tools.orders.get_gam_client")
    def test_auto_assigns_trafficker_when_not_provided(self, mock_get_client):
        """Test auto-assigns first user as trafficker when not provided."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_order_service = MagicMock()
        mock_user_service = MagicMock()

        mock_user_service.getUsersByStatement.return_value = {
            "results": [{"id": 999}]
        }
        mock_order_service.createOrders.return_value = [{
            "id": 123,
            "name": "New Order",
            "status": "DRAFT",
            "advertiserId": 456,
            "traffickerId": 999
        }]

        def get_service(name):
            if name == "UserService":
                return mock_user_service
            return mock_order_service

        mock_client.get_service.side_effect = get_service
        mock_client.create_statement.return_value = MagicMock()

        result = orders.create_order(order_name="New Order", advertiser_id=456)

        assert result["trafficker_id"] == 999

    @patch("gam_mcp.tools.orders.get_gam_client")
    def test_returns_error_on_creation_failure(self, mock_get_client):
        """Test returns error when order creation fails."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_order_service = MagicMock()
        mock_order_service.createOrders.return_value = None

        mock_client.get_service.return_value = mock_order_service
        mock_client.create_statement.return_value = MagicMock()

        result = orders.create_order(
            order_name="New Order",
            advertiser_id=456,
            trafficker_id=789
        )

        assert "error" in result
        assert "Failed to create order" in result["error"]


class TestFindOrCreateOrder:
    """Tests for find_or_create_order function."""

    @patch("gam_mcp.tools.orders.get_gam_client")
    def test_returns_existing_order_when_found(self, mock_get_client):
        """Test returns existing order when it exists."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_order_service = MagicMock()
        mock_order_service.getOrdersByStatement.return_value = {
            "results": [{
                "id": 123,
                "name": "Existing Order",
                "status": "APPROVED",
                "advertiserId": 456,
                "traffickerId": 789
            }]
        }

        mock_client.get_service.return_value = mock_order_service
        mock_client.create_statement.return_value = MagicMock()

        result = orders.find_or_create_order(
            order_name="Existing Order",
            advertiser_id=456
        )

        assert result["id"] == 123
        assert result["created"] is False
        assert "Found existing" in result["message"]

    @patch("gam_mcp.tools.orders.get_gam_client")
    @patch("gam_mcp.tools.orders.create_order")
    def test_creates_new_order_when_not_found(self, mock_create, mock_get_client):
        """Test creates new order when not found."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_order_service = MagicMock()
        mock_order_service.getOrdersByStatement.return_value = {"results": []}

        mock_client.get_service.return_value = mock_order_service
        mock_client.create_statement.return_value = MagicMock()

        mock_create.return_value = {
            "id": 123,
            "name": "New Order",
            "status": "DRAFT"
        }

        result = orders.find_or_create_order(
            order_name="New Order",
            advertiser_id=456
        )

        assert result["created"] is True
        mock_create.assert_called_once()
