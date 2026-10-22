"""Tests for delivery progress tools."""

import json
from unittest.mock import MagicMock, patch

import pytest

from gam_mcp.tools import delivery


def _zeep_line_item(**values):
    """Build a real zeep CompoundValue shaped like a delivering LineItem."""
    from zeep import xsd

    goal = xsd.ComplexType(xsd.Sequence([
        xsd.Element("goalType", xsd.String()),
        xsd.Element("unitType", xsd.String()),
        xsd.Element("units", xsd.Long()),
    ]))
    stats = xsd.ComplexType(xsd.Sequence([
        xsd.Element("impressionsDelivered", xsd.Long()),
        xsd.Element("clicksDelivered", xsd.Long()),
        xsd.Element("viewableImpressionsDelivered", xsd.Long()),
    ]))
    indicator = xsd.ComplexType(xsd.Sequence([
        xsd.Element("expectedDeliveryPercentage", xsd.Double()),
        xsd.Element("actualDeliveryPercentage", xsd.Double()),
    ]))
    line_item_type = xsd.ComplexType(xsd.Sequence([
        xsd.Element("id", xsd.Long()),
        xsd.Element("name", xsd.String()),
        xsd.Element("orderId", xsd.Long()),
        xsd.Element("status", xsd.String()),
        xsd.Element("startDateTime", xsd.AnyType()),
        xsd.Element("endDateTime", xsd.AnyType()),
        xsd.Element("primaryGoal", goal),
        xsd.Element("stats", stats),
        xsd.Element("deliveryIndicator", indicator),
    ]))
    return line_item_type(**values)


def _date_time(year, month, day):
    return {"date": {"year": year, "month": month, "day": day}}


ACME = _zeep_line_item(
    id=987654321,
    name="Acme 300x250",
    orderId=123456789,
    status="DELIVERING",
    startDateTime=_date_time(2026, 10, 1),
    endDateTime=_date_time(2026, 10, 31),
    primaryGoal={"goalType": "LIFETIME", "unitType": "IMPRESSIONS", "units": 500000},
    stats={"impressionsDelivered": 10000, "clicksDelivered": 12},
    deliveryIndicator={"expectedDeliveryPercentage": 75.0, "actualDeliveryPercentage": 2.0},
)


def _setup(mock_get_client, pages):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_service = MagicMock()
    mock_service.getLineItemsByStatement.side_effect = [{"results": page} for page in pages]
    mock_client.get_service.return_value = mock_service
    statement = MagicMock()
    mock_client.create_statement.return_value = statement
    return mock_service, statement


class TestGetDeliveryProgress:
    """Tests for get_delivery_progress function."""

    @patch("gam_mcp.tools.delivery.get_gam_client")
    def test_flags_line_item_behind_schedule(self, mock_get_client):
        """Test the handoff case: about 2% actual vs 75% expected is behind."""
        _setup(mock_get_client, [[ACME]])

        result = delivery.get_delivery_progress(order_id=123456789)

        entry = result["line_items"][0]
        assert entry == {
            "id": 987654321,
            "name": "Acme 300x250",
            "order_id": 123456789,
            "status": "DELIVERING",
            "start_date": "2026-10-01",
            "end_date": "2026-10-31",
            "goal_type": "LIFETIME",
            "goal_unit": "IMPRESSIONS",
            "goal": 500000,
            "delivered": 10000,
            "actual_pct": 2.0,
            "expected_pct": 75.0,
            "gap_pts": 73.0,
            "behind": True,
            "progress": "behind",
        }
        assert result["summary"] == {"total": 1, "behind": 1, "on_track": 0, "no_goal": 0}
        json.dumps(result)  # the MCP tool serialises the result

    @patch("gam_mcp.tools.delivery.get_gam_client")
    def test_filters_by_order_and_delivering_status(self, mock_get_client):
        """Test the statement filters on order and delivering statuses with a bind variable."""
        _, statement = _setup(mock_get_client, [[]])

        delivery.get_delivery_progress(order_id=42)

        where = statement.Where.call_args.args[0]
        assert "orderId = :orderId" in where
        assert "status IN ('DELIVERING', 'DELIVERY_EXTENDED')" in where
        statement.Where.return_value.WithBindVariable.assert_called_once_with('orderId', 42)

    @patch("gam_mcp.tools.delivery.get_gam_client")
    def test_without_order_covers_whole_network(self, mock_get_client):
        """Test omitting order_id filters only on status."""
        _, statement = _setup(mock_get_client, [[]])

        result = delivery.get_delivery_progress()

        assert statement.Where.call_args.args[0] == "status IN ('DELIVERING', 'DELIVERY_EXTENDED')"
        assert result["summary"]["total"] == 0

    @pytest.mark.parametrize("actual, expected, behind", [
        (65.0, 75.0, True),    # exactly at the threshold
        (65.5, 75.0, False),
        (90.0, 75.0, False),   # ahead of schedule
    ])
    @patch("gam_mcp.tools.delivery.get_gam_client")
    def test_threshold(self, mock_get_client, actual, expected, behind):
        """Test the behind flag uses expected minus actual against the threshold."""
        li = _zeep_line_item(
            id=1, name="LI",
            primaryGoal={"goalType": "LIFETIME", "unitType": "IMPRESSIONS", "units": 100},
            deliveryIndicator={"expectedDeliveryPercentage": expected, "actualDeliveryPercentage": actual},
        )
        _setup(mock_get_client, [[li]])

        entry = delivery.get_delivery_progress(behind_threshold_pts=10)["line_items"][0]

        assert entry["behind"] is behind
        assert entry["progress"] == ("behind" if behind else "on track")

    @patch("gam_mcp.tools.delivery.get_gam_client")
    def test_missing_indicator_is_no_goal_not_error(self, mock_get_client):
        """Test line items without a quantity goal are reported as no goal."""
        li = _zeep_line_item(
            id=2, name="Sponsorship", status="DELIVERING",
            primaryGoal={"goalType": "DAILY", "unitType": "IMPRESSIONS", "units": 100},
            stats={"impressionsDelivered": 5000},
            deliveryIndicator=None,
        )
        _setup(mock_get_client, [[li]])

        result = delivery.get_delivery_progress()

        entry = result["line_items"][0]
        assert entry["progress"] == "no goal"
        assert entry["behind"] is False
        assert entry["actual_pct"] is None and entry["gap_pts"] is None
        assert entry["delivered"] == 5000
        assert result["summary"]["no_goal"] == 1

    @patch("gam_mcp.tools.delivery.get_gam_client")
    def test_delivered_matches_goal_unit(self, mock_get_client):
        """Test click goals report delivered clicks, not impressions."""
        li = _zeep_line_item(
            id=3, name="CPC",
            primaryGoal={"goalType": "LIFETIME", "unitType": "CLICKS", "units": 1000},
            stats={"impressionsDelivered": 90000, "clicksDelivered": 400},
            deliveryIndicator={"expectedDeliveryPercentage": 50.0, "actualDeliveryPercentage": 40.0},
        )
        _setup(mock_get_client, [[li]])

        entry = delivery.get_delivery_progress()["line_items"][0]

        assert entry["delivered"] == 400
        assert entry["goal_unit"] == "CLICKS"

    @patch("gam_mcp.tools.delivery.get_gam_client")
    def test_sorted_furthest_behind_first_across_pages(self, mock_get_client):
        """Test all pages are read and results are sorted by gap, no-goal items last."""
        def li(id_, actual, expected):
            indicator = None if actual is None else {
                "expectedDeliveryPercentage": expected, "actualDeliveryPercentage": actual,
            }
            return _zeep_line_item(id=id_, name=f"LI{id_}", deliveryIndicator=indicator)

        page_size = delivery.fetch_all_pages.__defaults__[0]
        first_page = [li(i, 50.0, 50.0) for i in range(page_size - 2)] + [li(-1, None, None), li(-2, 10.0, 60.0)]
        mock_service, _ = _setup(mock_get_client, [first_page, [li(-3, 70.0, 90.0)]])

        result = delivery.get_delivery_progress()

        assert mock_service.getLineItemsByStatement.call_count == 2
        ids = [e["id"] for e in result["line_items"]]
        assert ids[:2] == [-2, -3]
        assert ids[-1] == -1
        assert result["summary"]["total"] == page_size + 1

    def test_rejects_negative_threshold(self):
        """Test a negative threshold is rejected before calling GAM."""
        result = delivery.get_delivery_progress(behind_threshold_pts=-1)
        assert "error" in result
