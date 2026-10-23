"""Preview plans for campaign evidence screenshots.

The server only asks GAM for one preview URL per active creative; the
screenshots themselves are taken on the caller's machine by `gam-evidence`
(see gam_mcp.evidence), so the server never runs a browser.
"""

import ipaddress
import logging
import os
from typing import Optional
from urllib.parse import urlsplit

from ..client import get_gam_client
from ..utils import fetch_all_pages, safe_get

logger = logging.getLogger(__name__)

SITE_URL_ENV = "GAM_EVIDENCE_SITE_URL"


class EvidenceError(ValueError):
    """Raised when a site or preview URL is not acceptable."""


def validate_site_url(url: str) -> str:
    """Check a URL is a public http(s) page.

    Rejects other schemes, IP addresses and local host names, so a preview
    can never point the screenshot browser at the caller's own network.

    Raises:
        EvidenceError: If the URL is not acceptable
    """
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise EvidenceError(f"Site URL must be an http(s) URL: {url}")
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip:
        raise EvidenceError(f"Site URL must use a host name, not an IP address: {url}")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise EvidenceError(f"Site URL must be a public host: {url}")
    return url.strip()


def _size_label(size) -> Optional[str]:
    width, height = safe_get(size, 'width'), safe_get(size, 'height')
    return f"{width}x{height}" if width and height else None


def _site_url_for(size: Optional[str], site_url: Optional[str], site_urls: dict) -> Optional[str]:
    return site_urls.get(size) or site_url or os.environ.get(SITE_URL_ENV, "").strip() or None


def get_preview_plan(
    order_id: Optional[int] = None,
    line_item_id: Optional[int] = None,
    site_url: Optional[str] = None,
    site_urls: Optional[dict] = None,
    network_code: Optional[str] = None,
) -> dict:
    """List every active creative of an order or line item with its preview URL.

    Returns:
        dict with the order and one item per line item and creative; items
        that cannot be previewed carry an "error" instead of a "preview_url"
    """
    if (order_id is None) == (line_item_id is None):
        return {"error": "Pass exactly one of order_id or line_item_id"}
    site_urls = site_urls or {}
    try:
        for url in [site_url, *site_urls.values()]:
            if url:
                validate_site_url(url)
    except EvidenceError as e:
        return {"error": str(e)}

    client = get_gam_client(network_code=network_code)
    line_item_service = client.get_service('LineItemService')
    lica_service = client.get_service('LineItemCreativeAssociationService')
    creative_service = client.get_service('CreativeService')
    order_service = client.get_service('OrderService')

    statement = client.create_statement()
    if line_item_id is not None:
        statement = statement.Where("id = :id").WithBindVariable('id', line_item_id)
    else:
        statement = statement.Where("orderId = :orderId").WithBindVariable('orderId', order_id)
    line_items = fetch_all_pages(line_item_service.getLineItemsByStatement, statement)
    if not line_items:
        target = f"line item {line_item_id}" if line_item_id is not None else f"order {order_id}"
        return {"error": f"No line items found for {target}"}

    order_id = safe_get(line_items[0], 'orderId')
    line_item_names = {safe_get(li, 'id'): safe_get(li, 'name') for li in line_items}
    ids = ", ".join(str(int(i)) for i in line_item_names)

    licas = fetch_all_pages(
        lica_service.getLineItemCreativeAssociationsByStatement,
        client.create_statement().Where(f"lineItemId IN ({ids}) AND status = 'ACTIVE'"),
    )

    creatives = {}
    creative_ids = {safe_get(lica, 'creativeId') for lica in licas}
    if creative_ids:
        id_list = ", ".join(str(int(i)) for i in creative_ids)
        for creative in fetch_all_pages(
            creative_service.getCreativesByStatement,
            client.create_statement().Where(f"id IN ({id_list})"),
        ):
            creatives[safe_get(creative, 'id')] = creative

    orders = order_service.getOrdersByStatement(
        client.create_statement().Where("id = :id").WithBindVariable('id', order_id).ToStatement()
    )
    order_results = safe_get(orders, 'results') or []
    order_name = safe_get(order_results[0], 'name') if order_results else None

    items = []
    for lica in licas:
        li_id = safe_get(lica, 'lineItemId')
        creative_id = safe_get(lica, 'creativeId')
        creative = creatives.get(creative_id)
        override_sizes = safe_get(lica, 'sizes') or []
        size = _size_label(override_sizes[0]) if override_sizes else _size_label(safe_get(creative, 'size'))
        item = {
            "line_item_id": li_id,
            "line_item_name": line_item_names.get(li_id),
            "creative_id": creative_id,
            "creative_name": safe_get(creative, 'name'),
            "size": size,
            "site_url": _site_url_for(size, site_url, site_urls),
        }
        items.append(item)

        if not item["site_url"]:
            item["error"] = (
                f"No site URL for size {size}: pass site_url, site_urls, or set {SITE_URL_ENV}"
            )
            continue
        try:
            validate_site_url(item["site_url"])
            item["preview_url"] = validate_site_url(
                lica_service.getPreviewUrl(li_id, creative_id, item["site_url"])
            )
        except Exception as e:
            item["error"] = str(e)

    items.sort(key=lambda i: (i["line_item_id"], i["creative_id"]))
    return {"order": {"id": order_id, "name": order_name}, "items": items}
