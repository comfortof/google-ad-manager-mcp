"""Delivery progress tools for Google Ad Manager."""

import logging
from typing import Optional

from ..client import get_gam_client
from ..utils import extract_date, fetch_all_pages, safe_get

logger = logging.getLogger(__name__)

# Line items GAM is actively pacing; DELIVERY_EXTENDED ones are past their end
# date on auto-extension days and almost always behind.
DELIVERING_STATUSES = ("DELIVERING", "DELIVERY_EXTENDED")

# Stats field holding the delivered amount for each goal unit type
DELIVERED_STAT_BY_UNIT = {
    "IMPRESSIONS": "impressionsDelivered",
    "CLICKS": "clicksDelivered",
    "VIEWABLE_IMPRESSIONS": "viewableImpressionsDelivered",
    "COMPLETED_VIEWS": "videoCompletionsDelivered",
}


def _round(value) -> Optional[float]:
    return round(float(value), 2) if value is not None else None


def _line_item_progress(li, behind_threshold_pts: float) -> dict:
    """Build the progress entry for one line item."""
    goal = safe_get(li, 'primaryGoal')
    unit_type = safe_get(goal, 'unitType')
    stats = safe_get(li, 'stats')
    delivered = safe_get(stats, DELIVERED_STAT_BY_UNIT.get(unit_type, 'impressionsDelivered'), 0) or 0

    entry = {
        "id": safe_get(li, 'id'),
        "name": safe_get(li, 'name'),
        "order_id": safe_get(li, 'orderId'),
        "status": safe_get(li, 'status'),
        "start_date": extract_date(safe_get(li, 'startDateTime')),
        "end_date": extract_date(safe_get(li, 'endDateTime')),
        "goal_type": safe_get(goal, 'goalType'),
        "goal_unit": unit_type,
        "goal": safe_get(goal, 'units'),
        "delivered": delivered,
        "actual_pct": None,
        "expected_pct": None,
        "gap_pts": None,
        "behind": False,
    }

    # GAM leaves deliveryIndicator empty for unlimited or percentage goals
    indicator = safe_get(li, 'deliveryIndicator')
    actual = safe_get(indicator, 'actualDeliveryPercentage')
    expected = safe_get(indicator, 'expectedDeliveryPercentage')
    if actual is None or expected is None:
        entry["progress"] = "no goal"
        return entry

    gap = expected - actual
    entry.update({
        "actual_pct": _round(actual),
        "expected_pct": _round(expected),
        "gap_pts": _round(gap),
        "behind": gap >= behind_threshold_pts,
    })
    entry["progress"] = "behind" if entry["behind"] else "on track"
    return entry


def get_delivery_progress(
    order_id: Optional[int] = None,
    behind_threshold_pts: float = 10,
    network_code: Optional[str] = None,
) -> dict:
    """Report actual vs expected delivery for delivering line items.

    Uses GAM's own delivery indicator, the same numbers as the UI "Progress" bar.

    Args:
        order_id: Optional order ID; all delivering line items in the network if omitted
        behind_threshold_pts: A line item is behind when expected minus actual
            delivery is at least this many percentage points
        network_code: Optional GAM network code. Uses default if not provided.

    Returns:
        dict with one entry per line item, sorted with the furthest behind first
    """
    if behind_threshold_pts < 0:
        return {"error": "behind_threshold_pts must be zero or positive"}

    client = get_gam_client(network_code=network_code)
    line_item_service = client.get_service('LineItemService')

    statuses = ", ".join(f"'{s}'" for s in DELIVERING_STATUSES)
    statement = client.create_statement()
    if order_id is not None:
        statement = statement.Where(
            f"orderId = :orderId AND status IN ({statuses})"
        ).WithBindVariable('orderId', order_id)
    else:
        statement = statement.Where(f"status IN ({statuses})")

    results = fetch_all_pages(line_item_service.getLineItemsByStatement, statement)
    line_items = [_line_item_progress(li, behind_threshold_pts) for li in results]
    line_items.sort(key=lambda e: (e["gap_pts"] is None, -(e["gap_pts"] or 0)))

    return {
        "order_id": order_id,
        "behind_threshold_pts": behind_threshold_pts,
        "summary": {
            "total": len(line_items),
            "behind": sum(1 for e in line_items if e["behind"]),
            "on_track": sum(1 for e in line_items if e["progress"] == "on track"),
            "no_goal": sum(1 for e in line_items if e["progress"] == "no goal"),
        },
        "line_items": line_items,
    }
