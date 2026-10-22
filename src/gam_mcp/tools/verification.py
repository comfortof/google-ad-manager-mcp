"""Verification tools for Google Ad Manager."""

import logging
from datetime import datetime, date
from typing import Optional
from ..client import get_gam_client
from ..utils import safe_get, extract_date, fetch_all_pages

logger = logging.getLogger(__name__)


def verify_line_item_setup(line_item_id: int, network_code: Optional[str] = None) -> dict:
    """Verify line item setup including creative placeholders and associations.

    Args:
        line_item_id: The line item ID to verify
        network_code: Optional GAM network code. Uses default if not provided.

    Returns:
        dict with verification results
    """
    client = get_gam_client(network_code=network_code)

    result = {
        "line_item_id": line_item_id,
        "line_item": None,
        "creative_placeholders": [],
        "creative_associations": [],
        "issues": [],
        "status": "OK"
    }

    # Get line item details using bind variable
    line_item_service = client.get_service('LineItemService')
    statement = client.create_statement()
    statement = statement.Where("id = :id").WithBindVariable('id', line_item_id)
    response = line_item_service.getLineItemsByStatement(statement.ToStatement())

    if 'results' not in response or len(response['results']) == 0:
        return {"error": f"Line item {line_item_id} not found"}

    line_item = response['results'][0]

    result["line_item"] = {
        "id": safe_get(line_item, 'id'),
        "name": safe_get(line_item, 'name'),
        "status": safe_get(line_item, 'status'),
        "type": safe_get(line_item, 'lineItemType'),
        "order_id": safe_get(line_item, 'orderId')
    }

    # Extract creative placeholders
    for ph in safe_get(line_item, 'creativePlaceholders') or []:
        size = safe_get(ph, 'size')
        width, height = safe_get(size, 'width'), safe_get(size, 'height')
        result["creative_placeholders"].append({
            "width": width,
            "height": height,
            "size_string": f"{width}x{height}"
        })

    # Get creative associations using bind variable
    lica_service = client.get_service('LineItemCreativeAssociationService')
    lica_statement = client.create_statement()
    lica_statement = lica_statement.Where(
        "lineItemId = :lineItemId"
    ).WithBindVariable('lineItemId', line_item_id)
    licas = fetch_all_pages(
        lica_service.getLineItemCreativeAssociationsByStatement, lica_statement
    )

    creative_service = client.get_service('CreativeService')

    if licas:
        for lica in licas:
            creative_id = safe_get(lica, 'creativeId')
            status = safe_get(lica, 'status', 'UNKNOWN')

            # Get creative details using bind variable
            creative_statement = client.create_statement()
            creative_statement = creative_statement.Where(
                "id = :id"
            ).WithBindVariable('id', creative_id)
            creative_response = creative_service.getCreativesByStatement(creative_statement.ToStatement())

            creative_info = {
                "creative_id": creative_id,
                "association_status": status,
                "creative_name": None,
                "creative_size": None,
                "size_overrides": []
            }

            creative_results = safe_get(creative_response, 'results') or []
            if len(creative_results) > 0:
                creative = creative_results[0]
                creative_size = safe_get(creative, 'size')
                creative_info["creative_name"] = safe_get(creative, 'name')
                creative_info["creative_size"] = (
                    f"{safe_get(creative_size, 'width')}x{safe_get(creative_size, 'height')}"
                )

            # Check for size overrides in LICA
            for size in safe_get(lica, 'sizes') or []:
                creative_info["size_overrides"].append(
                    f"{safe_get(size, 'width')}x{safe_get(size, 'height')}"
                )

            result["creative_associations"].append(creative_info)

    # Analyze for issues
    placeholder_sizes = set(p["size_string"] for p in result["creative_placeholders"])

    for assoc in result["creative_associations"]:
        creative_size = assoc["creative_size"]
        size_overrides = assoc["size_overrides"]

        # Determine effective sizes this creative targets
        effective_sizes = set(size_overrides) if size_overrides else {creative_size}

        # Check if any effective size matches a placeholder
        if not effective_sizes.intersection(placeholder_sizes):
            result["issues"].append({
                "type": "SIZE_MISMATCH",
                "creative_id": assoc["creative_id"],
                "creative_size": creative_size,
                "size_overrides": size_overrides,
                "available_placeholders": list(placeholder_sizes),
                "message": f"Creative {assoc['creative_id']} ({creative_size}) doesn't match any placeholder"
            })

    # Set overall status
    if result["issues"]:
        result["status"] = "ISSUES_FOUND"
    elif not result["creative_associations"]:
        result["status"] = "NO_CREATIVES"
        result["issues"].append({
            "type": "NO_CREATIVES",
            "message": "Line item has no creative associations"
        })

    # Summary
    result["summary"] = {
        "placeholder_count": len(result["creative_placeholders"]),
        "creative_count": len(result["creative_associations"]),
        "issue_count": len(result["issues"]),
        "placeholder_sizes": list(placeholder_sizes)
    }

    return result


def check_line_item_delivery_status(line_item_id: int, network_code: Optional[str] = None) -> dict:
    """Check detailed delivery status for a line item.

    Args:
        line_item_id: The line item ID to check
        network_code: Optional GAM network code. Uses default if not provided.

    Returns:
        dict with delivery status details
    """
    client = get_gam_client(network_code=network_code)
    line_item_service = client.get_service('LineItemService')

    statement = client.create_statement()
    statement = statement.Where("id = :id").WithBindVariable('id', line_item_id)
    response = line_item_service.getLineItemsByStatement(statement.ToStatement())

    if 'results' not in response or len(response['results']) == 0:
        return {"error": f"Line item {line_item_id} not found"}

    li = response['results'][0]
    stats = safe_get(li, 'stats') or {}
    primary_goal = safe_get(li, 'primaryGoal') or {}
    # GAM's own pacing figures; empty when the line item has no quantity goal
    delivery_indicator = safe_get(li, 'deliveryIndicator')

    # Calculate progress
    goal_units = safe_get(primary_goal, 'units', 0) or 0
    impressions = safe_get(stats, 'impressionsDelivered', 0) or 0

    progress_pct = 0
    if goal_units > 0:
        progress_pct = round((impressions / goal_units) * 100, 2)

    # Extract dates for pacing calculation
    start_date_str = extract_date(safe_get(li, 'startDateTime'))
    end_date_str = extract_date(safe_get(li, 'endDateTime'))

    # Calculate pacing (actual vs expected delivery based on time elapsed)
    pacing_pct = None
    expected_delivery = None
    days_elapsed = None
    total_days = None

    if start_date_str and end_date_str and goal_units > 0:
        try:
            start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            today = date.today()

            total_days = (end_dt - start_dt).days
            if total_days > 0:
                # Calculate days elapsed (capped at total_days)
                days_elapsed = min((today - start_dt).days, total_days)
                days_elapsed = max(days_elapsed, 0)  # Ensure non-negative

                # Expected delivery based on time elapsed
                time_fraction = days_elapsed / total_days
                expected_delivery = int(goal_units * time_fraction)

                # Pacing: actual / expected * 100
                if expected_delivery > 0:
                    pacing_pct = round((impressions / expected_delivery) * 100, 1)
                elif days_elapsed == 0:
                    # Campaign just started, no expected delivery yet
                    pacing_pct = 100.0 if impressions == 0 else None
        except (ValueError, TypeError):
            pass  # Date parsing failed, skip pacing calculation

    return {
        "line_item_id": safe_get(li, 'id'),
        "name": safe_get(li, 'name'),
        "status": safe_get(li, 'status'),
        "type": safe_get(li, 'lineItemType'),
        "start_date": start_date_str,
        "end_date": end_date_str,
        "delivery": {
            "impressions_delivered": impressions,
            "clicks_delivered": safe_get(stats, 'clicksDelivered', 0) or 0,
            "goal_type": safe_get(primary_goal, 'goalType'),
            "goal_unit_type": safe_get(primary_goal, 'unitType'),
            "goal_units": goal_units,
            "progress_percent": progress_pct,
            "expected_delivery": expected_delivery,
            "pacing_percent": pacing_pct,
            "days_elapsed": days_elapsed,
            "total_days": total_days
        },
        "needs_creatives": safe_get(li, 'isMissingCreatives', False),
        "delivery_indicator": {
            "expected_delivery_percentage": safe_get(delivery_indicator, 'expectedDeliveryPercentage'),
            "actual_delivery_percentage": safe_get(delivery_indicator, 'actualDeliveryPercentage'),
        } if delivery_indicator is not None else None,
        "delivery_rate_type": safe_get(li, 'deliveryRateType')
    }


def verify_order_setup(order_id: int, network_code: Optional[str] = None) -> dict:
    """Verify complete order setup including all line items.

    Args:
        order_id: The order ID to verify
        network_code: Optional GAM network code. Uses default if not provided.

    Returns:
        dict with complete order verification
    """
    client = get_gam_client(network_code=network_code)

    # Get order using bind variable
    order_service = client.get_service('OrderService')
    order_statement = client.create_statement()
    order_statement = order_statement.Where("id = :id").WithBindVariable('id', order_id)
    order_response = order_service.getOrdersByStatement(order_statement.ToStatement())

    if 'results' not in order_response or len(order_response['results']) == 0:
        return {"error": f"Order {order_id} not found"}

    order = order_response['results'][0]

    result = {
        "order_id": safe_get(order, 'id'),
        "order_name": safe_get(order, 'name'),
        "order_status": safe_get(order, 'status'),
        "advertiser_id": safe_get(order, 'advertiserId'),
        "line_items": [],
        "issues": [],
        "errors": [],
        "overall_status": "OK"
    }

    # Get all line items using bind variable
    line_item_service = client.get_service('LineItemService')
    li_statement = client.create_statement()
    li_statement = li_statement.Where("orderId = :orderId").WithBindVariable('orderId', order_id)
    order_line_items = fetch_all_pages(line_item_service.getLineItemsByStatement, li_statement)

    for li in order_line_items:
        li_id = safe_get(li, 'id')
        li_name = safe_get(li, 'name')
        try:
            li_verification = verify_line_item_setup(li_id, network_code=network_code)
        except Exception as e:
            logger.exception("Verification failed for line item %s", li_id)
            li_verification = {"error": f"Verification failed: {e}"}

        if "error" in li_verification:
            result["line_items"].append({
                "id": li_id,
                "name": li_name,
                "error": li_verification["error"]
            })
            result["errors"].append({
                "line_item_id": li_id,
                "line_item_name": li_name,
                "error": li_verification["error"]
            })
        else:
            result["line_items"].append({
                "id": li_id,
                "name": li_verification["line_item"]["name"],
                "status": li_verification["line_item"]["status"],
                "creative_count": li_verification["summary"]["creative_count"],
                "issue_count": li_verification["summary"]["issue_count"],
                "issues": li_verification["issues"]
            })

            if li_verification["issues"]:
                result["issues"].extend([
                    {**issue, "line_item_id": li_id, "line_item_name": li_name}
                    for issue in li_verification["issues"]
                ])

    # Set overall status. A line item that could not be verified must not
    # leave the order looking OK.
    if result["errors"]:
        result["overall_status"] = "INCOMPLETE"
    elif result["issues"]:
        result["overall_status"] = "ISSUES_FOUND"

    result["summary"] = {
        "line_item_count": len(result["line_items"]),
        "total_issues": len(result["issues"]),
        "unverified_line_items": len(result["errors"])
    }

    return result
