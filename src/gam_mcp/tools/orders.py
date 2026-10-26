"""Order-related tools for Google Ad Manager."""

import logging
from datetime import datetime, date
from typing import Optional
from ..client import get_gam_client
from ..utils import safe_get, extract_date, fetch_all_pages

logger = logging.getLogger(__name__)


ORDER_BATCH_SIZE = 400


def _delivering_line_item(li) -> dict:
    """Delivery, goal and pacing figures for one line item."""
    # Extract dates safely
    start_date = extract_date(safe_get(li, 'startDateTime'))
    end_date = extract_date(safe_get(li, 'endDateTime'))

    # Extract stats
    stats = safe_get(li, 'stats')
    impressions = safe_get(stats, 'impressionsDelivered', 0) or 0
    clicks = safe_get(stats, 'clicksDelivered', 0) or 0

    # Extract primary goal
    primary_goal = safe_get(li, 'primaryGoal')
    goal_type = safe_get(primary_goal, 'goalType')
    goal_unit_type = safe_get(primary_goal, 'unitType')
    goal_units = safe_get(primary_goal, 'units', 0) or 0

    # Calculate progress
    progress_pct = 0
    if goal_units > 0:
        progress_pct = round((impressions / goal_units) * 100, 2)

    # Calculate pacing
    pacing_pct = None
    expected_delivery = None
    days_elapsed = None
    total_days = None

    if start_date and end_date and goal_units > 0:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
            today = date.today()

            total_days = (end_dt - start_dt).days
            if total_days > 0:
                days_elapsed = min((today - start_dt).days, total_days)
                days_elapsed = max(days_elapsed, 0)

                time_fraction = days_elapsed / total_days
                expected_delivery = int(goal_units * time_fraction)

                if expected_delivery > 0:
                    pacing_pct = round((impressions / expected_delivery) * 100, 1)
                elif days_elapsed == 0:
                    pacing_pct = 100.0 if impressions == 0 else None
        except (ValueError, TypeError):
            pass

    return {
        "id": safe_get(li, 'id'),
        "name": safe_get(li, 'name'),
        "status": safe_get(li, 'status'),
        "type": safe_get(li, 'lineItemType'),
        "start_date": start_date,
        "end_date": end_date,
        "impressions_delivered": impressions,
        "clicks_delivered": clicks,
        "goal_type": goal_type,
        "goal_unit_type": goal_unit_type,
        "goal_units": goal_units,
        "progress_percent": progress_pct,
        "expected_delivery": expected_delivery,
        "pacing_percent": pacing_pct,
        "days_elapsed": days_elapsed,
        "total_days": total_days
    }


def _fetch_orders(client, order_ids) -> dict:
    """Orders by ID, fetched in batches rather than one call per order."""
    order_service = client.get_service('OrderService')
    orders = {}
    for i in range(0, len(order_ids), ORDER_BATCH_SIZE):
        id_list = ", ".join(str(int(o)) for o in order_ids[i:i + ORDER_BATCH_SIZE])
        statement = client.create_statement().Where(f"id IN ({id_list})")
        for order in fetch_all_pages(order_service.getOrdersByStatement, statement):
            orders[safe_get(order, 'id')] = order
    return orders


def list_delivering_orders(include_line_items: bool = False, network_code: Optional[str] = None) -> dict:
    """List all orders with line items currently delivering.

    Args:
        include_line_items: Include each delivering line item with its delivery,
            goal and pacing figures. Off by default: across a whole network the
            full list can run to megabytes.
        network_code: Optional GAM network code. Uses default if not provided.

    Returns:
        dict with orders, each with its delivering line item count and
        delivered impressions and clicks, plus the line items when requested
    """
    client = get_gam_client(network_code=network_code)

    line_item_service = client.get_service('LineItemService')

    # Get delivering line items using bind variable
    statement = client.create_statement()
    statement = statement.Where(
        "status = :status"
    ).WithBindVariable('status', 'DELIVERING').OrderBy('orderId', ascending=True)

    line_items = fetch_all_pages(line_item_service.getLineItemsByStatement, statement)

    if not line_items:
        return {"orders": [], "message": "No delivering line items found"}

    # Group by order ID
    orders_dict = {}
    for li in line_items:
        orders_dict.setdefault(safe_get(li, 'orderId'), []).append(li)

    orders = _fetch_orders(client, list(orders_dict))

    result_orders = []
    for order_id, order_line_items in orders_dict.items():
        order = orders.get(order_id)
        if order is None:
            continue
        entries = [_delivering_line_item(li) for li in order_line_items]
        order_data = {
            "id": safe_get(order, 'id'),
            "name": safe_get(order, 'name'),
            "status": safe_get(order, 'status'),
            "advertiser_id": safe_get(order, 'advertiserId'),
            "trafficker_id": safe_get(order, 'traffickerId'),
            "delivering_line_items": len(entries),
            "impressions_delivered": sum(e["impressions_delivered"] for e in entries),
            "clicks_delivered": sum(e["clicks_delivered"] for e in entries),
        }
        if include_line_items:
            order_data["line_items"] = entries
        result_orders.append(order_data)

    result = {
        "orders": result_orders,
        "total_orders": len(result_orders),
        "total_line_items": sum(o["delivering_line_items"] for o in result_orders)
    }
    if not include_line_items:
        result["message"] = (
            "Summary per order. Pass include_line_items=true for line item details, "
            "or use list_line_items_by_order / get_delivery_progress for one order."
        )
    return result


def get_order(
    order_id: Optional[int] = None,
    order_name: Optional[str] = None,
    network_code: Optional[str] = None
) -> dict:
    """Get order details by ID or name.

    Args:
        order_id: Order ID (optional if name provided)
        order_name: Order name to search for (optional if ID provided)
        network_code: Optional GAM network code. Uses default if not provided.

    Returns:
        dict with order details
    """
    if not order_id and not order_name:
        return {"error": "Either order_id or order_name must be provided"}

    client = get_gam_client(network_code=network_code)
    order_service = client.get_service('OrderService')

    statement = client.create_statement()

    if order_id:
        statement = statement.Where("id = :id").WithBindVariable('id', order_id)
    else:
        statement = statement.Where("name = :name").WithBindVariable('name', order_name)

    response = order_service.getOrdersByStatement(statement.ToStatement())

    if 'results' not in response or len(response['results']) == 0:
        return {"error": f"Order not found"}

    order = response['results'][0]

    # Get line items for this order
    line_item_service = client.get_service('LineItemService')
    li_statement = client.create_statement()
    li_statement = li_statement.Where(
        "orderId = :orderId"
    ).WithBindVariable('orderId', safe_get(order, 'id'))
    line_items = []
    for li in fetch_all_pages(line_item_service.getLineItemsByStatement, li_statement):
        line_items.append({
            "id": safe_get(li, 'id'),
            "name": safe_get(li, 'name'),
            "status": safe_get(li, 'status'),
            "type": safe_get(li, 'lineItemType')
        })

    return {
        "id": safe_get(order, 'id'),
        "name": safe_get(order, 'name'),
        "status": safe_get(order, 'status'),
        "advertiser_id": safe_get(order, 'advertiserId'),
        "trafficker_id": safe_get(order, 'traffickerId'),
        "line_items": line_items,
        "total_line_items": len(line_items)
    }


def create_order(
    order_name: str,
    advertiser_id: int,
    trafficker_id: Optional[int] = None,
    network_code: Optional[str] = None
) -> dict:
    """Create a new order.

    Args:
        order_name: Name for the order
        advertiser_id: ID of the advertiser company
        trafficker_id: ID of the trafficker user (optional, defaults to first user)
        network_code: Optional GAM network code. Uses default if not provided.

    Returns:
        dict with created order details
    """
    client = get_gam_client(network_code=network_code)
    order_service = client.get_service('OrderService')

    # If no trafficker provided, get the first user
    if trafficker_id is None:
        user_service = client.get_service('UserService')
        user_statement = client.create_statement().Limit(1)
        user_response = user_service.getUsersByStatement(user_statement.ToStatement())

        if 'results' in user_response and len(user_response['results']) > 0:
            trafficker_id = safe_get(user_response['results'][0], 'id')

    new_order = {
        'name': order_name,
        'advertiserId': advertiser_id,
        'traffickerId': trafficker_id,
    }

    created_orders = order_service.createOrders([new_order])

    if not created_orders:
        return {"error": "Failed to create order"}

    order = created_orders[0]

    return {
        "id": safe_get(order, 'id'),
        "name": safe_get(order, 'name'),
        "status": safe_get(order, 'status'),
        "advertiser_id": safe_get(order, 'advertiserId'),
        "trafficker_id": safe_get(order, 'traffickerId'),
        "message": f"Order '{order_name}' created successfully"
    }


def find_or_create_order(
    order_name: str,
    advertiser_id: int,
    trafficker_id: Optional[int] = None,
    network_code: Optional[str] = None
) -> dict:
    """Find an existing order or create a new one.

    Args:
        order_name: Name for the order
        advertiser_id: ID of the advertiser company
        trafficker_id: ID of the trafficker user (optional)
        network_code: Optional GAM network code. Uses default if not provided.

    Returns:
        dict with order details (existing or newly created)
    """
    client = get_gam_client(network_code=network_code)
    order_service = client.get_service('OrderService')

    # Use bind variables for safe query
    statement = client.create_statement()
    statement = statement.Where(
        "name = :name AND advertiserId = :advertiserId"
    ).WithBindVariable('name', order_name).WithBindVariable('advertiserId', advertiser_id)

    response = order_service.getOrdersByStatement(statement.ToStatement())

    if 'results' in response and len(response['results']) > 0:
        order = response['results'][0]
        return {
            "id": safe_get(order, 'id'),
            "name": safe_get(order, 'name'),
            "status": safe_get(order, 'status'),
            "advertiser_id": safe_get(order, 'advertiserId'),
            "trafficker_id": safe_get(order, 'traffickerId'),
            "created": False,
            "message": f"Found existing order '{order_name}'"
        }

    # Create new order
    result = create_order(order_name, advertiser_id, trafficker_id, network_code=network_code)
    if "error" not in result:
        result["created"] = True
    return result
