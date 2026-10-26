"""Ad unit discovery: what an ad unit is, which line items reach it, and
which pages it serves on.

Ad Manager keeps no list of pages per ad unit. Three read-only sources come
closest: the ad unit itself (code, path, sizes, description), line item
inventory targeting, and Historical reports broken down by site or URL.
"""

import logging
from typing import Optional

from ..client import get_gam_client
from ..utils import fetch_all_pages, safe_get
from .reporting import run_custom_report

logger = logging.getLogger(__name__)

STATUSES = ("ACTIVE", "INACTIVE", "ARCHIVED")

# Report dimension per page breakdown; URL only covers some demand (see below).
# DOMAIN is left out: GAM rejects it with line item level impressions.
PAGE_DIMENSIONS = {"SITE": "SITE_NAME", "URL": "URL_NAME"}
PAGE_METRIC = "TOTAL_LINE_ITEM_LEVEL_IMPRESSIONS"
URL_CAVEAT = (
    "URL rows only cover Ad Exchange and Open Bidding traffic on sites defined "
    "under Inventory > Sites; other demand shows as '(Not applicable)'. Use "
    "by='SITE' for all demand."
)


def _size_label(ad_unit_size) -> Optional[str]:
    label = safe_get(ad_unit_size, 'fullDisplayString')
    if label:
        return label
    size = safe_get(ad_unit_size, 'size')
    width, height = safe_get(size, 'width'), safe_get(size, 'height')
    return f"{width}x{height}" if width and height else None


def gpt_path(ad_unit, network_code: str) -> str:
    """The ad unit path a page passes to GPT, e.g. /123/section/slot.

    parentPath starts at the root ad unit, which GPT addresses by network code.
    """
    parents = list(safe_get(ad_unit, 'parentPath') or [])
    if not parents:
        return f"/{network_code}"
    codes = [safe_get(p, 'adUnitCode') for p in parents[1:]] + [safe_get(ad_unit, 'adUnitCode')]
    return "/" + "/".join([network_code, *codes])


def _ad_unit_entry(ad_unit, network_code: str) -> dict:
    parents = list(safe_get(ad_unit, 'parentPath') or [])
    names = [safe_get(p, 'name') for p in parents] + [safe_get(ad_unit, 'name')]
    return {
        "id": safe_get(ad_unit, 'id'),
        "name": safe_get(ad_unit, 'name'),
        "code": safe_get(ad_unit, 'adUnitCode'),
        "gpt_path": gpt_path(ad_unit, network_code),
        "name_path": " > ".join(n for n in names if n),
        "is_root": not parents,
        "parent_id": safe_get(ad_unit, 'parentId'),
        "has_children": bool(safe_get(ad_unit, 'hasChildren')),
        "status": safe_get(ad_unit, 'status'),
        "description": safe_get(ad_unit, 'description') or None,
        "sizes": [s for s in (_size_label(u) for u in safe_get(ad_unit, 'adUnitSizes') or []) if s],
        "target_window": safe_get(ad_unit, 'targetWindow'),
        "explicitly_targeted": bool(safe_get(ad_unit, 'explicitlyTargeted')),
    }


def _fetch_ad_units(client, ids) -> dict:
    """Ad units by ID, as {str(id): entry}: GAM returns ad unit IDs as strings."""
    if not ids:
        return {}
    id_list = ", ".join(str(int(i)) for i in ids)
    units = fetch_all_pages(
        client.get_service('InventoryService').getAdUnitsByStatement,
        client.create_statement().Where(f"id IN ({id_list})"),
    )
    return {str(safe_get(u, 'id')): _ad_unit_entry(u, client.network_code) for u in units}


def find_ad_units(
    ad_unit_id: Optional[int] = None,
    name: Optional[str] = None,
    code: Optional[str] = None,
    parent_id: Optional[int] = None,
    status: Optional[str] = "ACTIVE",
    limit: int = 200,
    network_code: Optional[str] = None,
) -> dict:
    """Look up ad units by ID, name (substring), code or parent.

    Returns:
        dict with the matching ad units, each with its GPT path, sizes and
        description, and whether the list was cut at limit
    """
    if status is not None and status not in STATUSES:
        return {"error": f"status must be one of {', '.join(STATUSES)} or null"}

    client = get_gam_client(network_code=network_code)
    clauses, binds = [], {}
    if ad_unit_id is not None:
        clauses.append("id = :id")
        binds['id'] = int(ad_unit_id)
    if name:
        clauses.append("name LIKE :name")
        binds['name'] = f"%{name}%"
    if code:
        clauses.append("adUnitCode = :code")
        binds['code'] = code
    if parent_id is not None:
        clauses.append("parentId = :parentId")
        binds['parentId'] = int(parent_id)
    if status is not None and ad_unit_id is None:
        clauses.append("status = :status")
        binds['status'] = status

    statement = client.create_statement()
    if clauses:
        statement = statement.Where(" AND ".join(clauses))
        for key, value in binds.items():
            statement = statement.WithBindVariable(key, value)

    units = fetch_all_pages(client.get_service('InventoryService').getAdUnitsByStatement, statement)
    entries = sorted(
        (_ad_unit_entry(u, client.network_code) for u in units), key=lambda e: e["gpt_path"],
    )
    if not entries:
        return {"error": "No ad units match", "ad_units": []}
    return {
        "count": len(entries),
        "truncated": len(entries) > limit,
        "ad_units": entries[:limit],
    }


def get_line_item_inventory(line_item_id: int, network_code: Optional[str] = None) -> dict:
    """Resolve a line item's inventory targeting to ad unit paths.

    Returns:
        dict with the targeted and excluded ad units (with GPT paths and
        whether descendants are included), targeted placements and their ad
        units, and run_of_network when the line item targets the root
    """
    client = get_gam_client(network_code=network_code)
    response = client.get_service('LineItemService').getLineItemsByStatement(
        client.create_statement().Where("id = :id").WithBindVariable('id', int(line_item_id)).ToStatement()
    )
    results = safe_get(response, 'results') or []
    if not results:
        return {"error": f"Line item {line_item_id} not found"}
    line_item = results[0]
    inventory = safe_get(safe_get(line_item, 'targeting'), 'inventoryTargeting')
    targeted = list(safe_get(inventory, 'targetedAdUnits') or [])
    excluded = list(safe_get(inventory, 'excludedAdUnits') or [])
    placement_ids = list(safe_get(inventory, 'targetedPlacementIds') or [])

    placements = []
    if placement_ids:
        id_list = ", ".join(str(int(i)) for i in placement_ids)
        placements = fetch_all_pages(
            client.get_service('PlacementService').getPlacementsByStatement,
            client.create_statement().Where(f"id IN ({id_list})"),
        )

    unit_ids = {safe_get(t, 'adUnitId') for t in targeted + excluded}
    for placement in placements:
        unit_ids.update(safe_get(placement, 'targetedAdUnitIds') or [])
    units = _fetch_ad_units(client, sorted(i for i in unit_ids if i))

    def _resolve(targets):
        resolved = []
        for t in targets:
            unit_id = safe_get(t, 'adUnitId')
            entry = dict(units.get(str(unit_id)) or {"id": unit_id, "error": "Ad unit not found"})
            entry["include_descendants"] = safe_get(t, 'includeDescendants', True) is not False
            resolved.append(entry)
        return resolved

    included = _resolve(targeted)
    return {
        "line_item_id": safe_get(line_item, 'id'),
        "line_item_name": safe_get(line_item, 'name'),
        "order_id": safe_get(line_item, 'orderId'),
        "sizes": [
            s for s in (
                _size_label({'size': safe_get(p, 'size')})
                for p in safe_get(line_item, 'creativePlaceholders') or []
            ) if s
        ],
        "run_of_network": any(u.get("is_root") and u["include_descendants"] for u in included),
        "targeted_ad_units": included,
        "excluded_ad_units": _resolve(excluded),
        "targeted_placements": [
            {
                "id": safe_get(p, 'id'),
                "name": safe_get(p, 'name'),
                "ad_units": [
                    units.get(str(i)) or {"id": i, "error": "Ad unit not found"}
                    for i in safe_get(p, 'targetedAdUnitIds') or []
                ],
            }
            for p in placements
        ],
    }


def _to_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def get_ad_unit_pages(
    ad_unit_id: int,
    by: str = "SITE",
    date_range_type: str = "LAST_WEEK",
    include_descendants: bool = True,
    limit: int = 50,
    timeout_seconds: int = 120,
    network_code: Optional[str] = None,
) -> dict:
    """Where an ad unit served: impressions per site or URL.

    Returns:
        dict with rows of {by value, impressions}, most impressions first
    """
    by = by.upper()
    if by not in PAGE_DIMENSIONS:
        return {"error": f"by must be one of {', '.join(PAGE_DIMENSIONS)}"}
    dimension = PAGE_DIMENSIONS[by]

    units = _fetch_ad_units(get_gam_client(network_code=network_code), [ad_unit_id])
    if not units:
        return {"error": f"Ad unit {ad_unit_id} not found"}
    unit = units[str(int(ad_unit_id))]
    # PARENT_AD_UNIT_ID matches nothing for the root, whose descendants are the
    # whole network, so the root with descendants runs unfiltered
    if include_descendants and unit["is_root"]:
        filter_statement = None
    elif include_descendants:
        filter_statement = f"PARENT_AD_UNIT_ID = {int(ad_unit_id)}"
    else:
        filter_statement = f"AD_UNIT_ID = {int(ad_unit_id)}"

    report = run_custom_report(
        dimensions=[dimension],
        columns=[PAGE_METRIC],
        date_range_type=date_range_type,
        filter_statement=filter_statement,
        timeout_seconds=timeout_seconds,
        network_code=network_code,
    )
    if "error" in report:
        return report

    # CSV_DUMP headers look like "Dimension.SITE_NAME" and "Column.TOTAL_..."
    headers = report.get("headers") or []
    try:
        key_index = next(i for i, h in enumerate(headers) if h.endswith(dimension))
        value_index = next(i for i, h in enumerate(headers) if h.endswith(PAGE_METRIC))
    except StopIteration:
        return {"error": f"Unexpected report headers: {headers}"}

    rows = sorted(
        (
            {by.lower(): row[key_index], "impressions": _to_int(row[value_index])}
            for row in report.get("data") or []
            if len(row) > max(key_index, value_index)
        ),
        key=lambda r: r["impressions"],
        reverse=True,
    )
    result = {
        "ad_unit_id": int(ad_unit_id),
        "gpt_path": unit["gpt_path"],
        "include_descendants": include_descendants,
        "by": by,
        "date_range_type": date_range_type,
        "count": len(rows),
        "truncated": len(rows) > limit,
        "rows": rows[:limit],
    }
    notes = ["'(Not applicable)' collects impressions GAM could not attribute to a site or URL."]
    if by == "URL":
        notes.append(URL_CAVEAT)
    result["notes"] = notes
    return result
