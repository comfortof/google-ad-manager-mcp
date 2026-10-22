"""Agency evidence screenshots for Google Ad Manager campaigns.

Each creative is forced into its slot with a GAM preview URL, so there is no
refreshing to catch a rotating banner. Capturing needs the optional
`screenshots` extra (Playwright) and a Chromium build:

    uv sync --extra screenshots && uv run playwright install chromium
"""

import asyncio
import base64
import html
import ipaddress
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import urlsplit

from ..client import get_gam_client
from ..utils import fetch_all_pages, safe_get

logger = logging.getLogger(__name__)

EVIDENCE_DIR_ENV = "GAM_EVIDENCE_DIR"
SITE_URL_ENV = "GAM_EVIDENCE_SITE_URL"
ALLOWED_HOSTS_ENV = "GAM_EVIDENCE_ALLOWED_HOSTS"

# Playwright device presets, with the viewport widths agencies ask for
VIEWPORTS = {
    "desktop": {"device": "Desktop Chrome", "viewport": {"width": 1440, "height": 900}},
    "mobile": {"device": "iPhone 13", "viewport": {"width": 390, "height": 844}},
}
DEFAULT_VIEWPORTS = ("desktop", "mobile")

# Accept buttons of common consent platforms, then visible text fallbacks
CONSENT_SELECTORS = (
    "#onetrust-accept-btn-handler",
    "#didomi-notice-agree-button",
    "button.fc-cta-consent",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    ".qc-cmp2-summary-buttons button[mode='primary']",
    "button:has-text('Sutinku')",
    "button:has-text('Priimti visus')",
    "button:has-text('Accept all')",
    "button:has-text('I agree')",
)

NAVIGATION_TIMEOUT_MS = 45_000
SLOT_SEARCH_STEPS = 25
SLOT_SEARCH_PAUSE_MS = 800
SLOT_SETTLE_MS = 1_500

# Returns the element ID of the GPT slot that rendered this creative. Prefer a
# slot matching both IDs; fall back to the creative alone.
FIND_SLOT_JS = """
([lineItemId, creativeId]) => {
  const gt = window.googletag;
  if (!gt || typeof gt.pubads !== 'function') return null;
  let creativeOnly = null;
  for (const slot of gt.pubads().getSlots()) {
    const info = slot.getResponseInformation();
    if (!info || String(info.creativeId) !== creativeId) continue;
    if (String(info.lineItemId) === lineItemId) return slot.getSlotElementId();
    creativeOnly = creativeOnly || slot.getSlotElementId();
  }
  return creativeOnly;
}
"""

ProgressCallback = Callable[[int, int], Awaitable[None]]


class EvidenceError(ValueError):
    """Raised when a site URL or the evidence directory is not acceptable."""


def get_evidence_root() -> Path:
    """Return the directory evidence is written to, creating it if needed."""
    root = Path(os.environ.get(EVIDENCE_DIR_ENV, "").strip() or "evidence").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _allowed_hosts() -> list[str]:
    value = os.environ.get(ALLOWED_HOSTS_ENV, "")
    return [h.strip().lower().lstrip(".") for h in value.split(",") if h.strip()]


def validate_site_url(url: str) -> str:
    """Check a URL is safe for the headless browser to open.

    Only http(s) URLs with a public host name are accepted, so the browser
    cannot be pointed at localhost or internal addresses. When
    GAM_EVIDENCE_ALLOWED_HOSTS is set, the host must be one of those domains
    or a subdomain of one.

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

    allowed = _allowed_hosts()
    if allowed and not any(host == d or host.endswith(f".{d}") for d in allowed):
        raise EvidenceError(f"Site URL host '{host}' is not in {ALLOWED_HOSTS_ENV}")
    return url.strip()


def _size_label(size) -> Optional[str]:
    width, height = safe_get(size, 'width'), safe_get(size, 'height')
    return f"{width}x{height}" if width and height else None


def _site_url_for(size: Optional[str], site_url: Optional[str], site_urls: dict) -> Optional[str]:
    return site_urls.get(size) or site_url or os.environ.get(SITE_URL_ENV, "").strip() or None


def plan_captures(
    order_id: Optional[int] = None,
    line_item_id: Optional[int] = None,
    site_url: Optional[str] = None,
    site_urls: Optional[dict] = None,
    network_code: Optional[str] = None,
) -> dict:
    """List every active creative to capture and get its preview URL from GAM.

    Returns:
        dict with the order and one item per line item and creative; items
        that cannot be previewed carry an "error" instead of a "preview_url"
    """
    site_urls = site_urls or {}
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


async def _accept_consent(page) -> bool:
    """Click the first visible consent accept button, in the page or a CMP iframe."""
    for frame in page.frames:
        for selector in CONSENT_SELECTORS:
            button = frame.locator(selector).first
            try:
                if await button.is_visible():
                    await button.click(timeout=3_000)
                    await page.wait_for_timeout(500)
                    return True
            except Exception:
                continue
    return False


async def _find_slot(page, line_item_id: int, creative_id: int) -> Optional[str]:
    """Scroll down until the slot showing the creative has rendered."""
    args = [str(line_item_id), str(creative_id)]
    for _ in range(SLOT_SEARCH_STEPS):
        slot_id = await page.evaluate(FIND_SLOT_JS, args)
        if slot_id:
            return slot_id
        at_bottom = await page.evaluate(
            "() => { window.scrollBy(0, window.innerHeight * 0.8);"
            " return window.innerHeight + window.scrollY >= document.body.scrollHeight; }"
        )
        await page.wait_for_timeout(SLOT_SEARCH_PAUSE_MS)
        if at_bottom:
            return await page.evaluate(FIND_SLOT_JS, args)
    return None


async def _capture_one(context, item: dict, viewport: str, out_dir: Path) -> dict:
    """Open one preview URL and save the full-page and slot-only PNGs."""
    result = {"viewport": viewport, "slot_found": False}
    page = await context.new_page()
    try:
        await page.goto(item["preview_url"], wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
        try:
            await page.wait_for_load_state("load", timeout=15_000)
        except Exception:
            pass  # slow third-party scripts; the slot search below still waits
        result["consent_accepted"] = await _accept_consent(page)

        slot_id = await _find_slot(page, item["line_item_id"], item["creative_id"])
        stem = f"{item['creative_id']}_{viewport}"
        if slot_id:
            slot = await page.evaluate_handle("id => document.getElementById(id)", slot_id)
            element = slot.as_element()
            if element:
                await element.scroll_into_view_if_needed()
                await page.wait_for_timeout(SLOT_SETTLE_MS)
                slot_path = out_dir / f"{stem}_slot.png"
                await element.screenshot(path=str(slot_path))
                result.update({"slot_found": True, "slot_png": str(slot_path)})

        page_path = out_dir / f"{stem}.png"
        await page.screenshot(path=str(page_path), full_page=True)
        result["page_png"] = str(page_path)
        if not result["slot_found"]:
            result["warning"] = (
                "Creative slot not found on the page; check the page requests "
                "this ad unit and size. Saved the full page only."
            )
    except Exception as e:
        result["error"] = str(e)
    finally:
        await page.close()
    return result


def _image_tag(path: Optional[str], css_class: str) -> str:
    if not path:
        return ""
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f'<img class="{css_class}" src="data:image/png;base64,{data}">'


def _evidence_html(order: dict, items: list, captured_at: str) -> str:
    """Build the HTML the combined PDF is printed from."""
    esc = lambda value: html.escape(str(value if value is not None else "-"))  # noqa: E731
    sections = []
    for item in items:
        for capture in item.get("captures", []):
            if not capture.get("page_png"):
                continue
            sections.append(
                '<section>'
                f'<h2>{esc(item["creative_name"])} ({esc(item["creative_id"])})</h2>'
                f'<p>Line item: {esc(item["line_item_name"])} ({esc(item["line_item_id"])})<br>'
                f'Size: {esc(item["size"])} · Viewport: {esc(capture["viewport"])}<br>'
                f'Page: {esc(item["site_url"])}</p>'
                f'{_image_tag(capture.get("slot_png"), "slot")}'
                f'{_image_tag(capture.get("page_png"), "page")}'
                '</section>'
            )
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "body{font-family:sans-serif;font-size:11px;margin:0}"
        "h1{font-size:18px}h2{font-size:14px;margin:0 0 4px}"
        "section{break-before:page}section:first-of-type{break-before:auto}"
        "img{display:block;max-width:100%;margin:8px 0;border:1px solid #ccc}"
        "img.slot{max-height:70mm;width:auto}"
        "img.page{max-height:170mm;width:auto}"
        "</style></head><body>"
        f"<h1>Campaign evidence: {esc(order['name'])} ({esc(order['id'])})</h1>"
        f"<p>Captured {esc(captured_at)}</p>"
        + "".join(sections)
        + "</body></html>"
    )


async def capture_campaign_screenshots(
    order_id: Optional[int] = None,
    line_item_id: Optional[int] = None,
    site_url: Optional[str] = None,
    site_urls: Optional[dict] = None,
    viewports: Optional[list] = None,
    network_code: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> dict:
    """Screenshot every active creative of an order or line item on a real page.

    Each creative is forced into its slot with a GAM preview URL. Saves
    evidence/<order>/<line item>/<creative>_<viewport>.png (full page) and
    <creative>_<viewport>_slot.png (slot only), plus evidence/<order>/evidence.pdf.

    Args:
        order_id: Capture every line item of this order
        line_item_id: Capture a single line item (instead of order_id)
        site_url: Page to preview creatives on, for sizes not in site_urls
        site_urls: Page per creative size, e.g. {"300x250": "https://..."}
        viewports: Viewport names from VIEWPORTS; defaults to desktop and mobile
        network_code: Optional GAM network code. Uses default if not provided.
        on_progress: Optional async callback receiving (done, total) captures

    Returns:
        dict with the files written per creative and viewport
    """
    if (order_id is None) == (line_item_id is None):
        return {"error": "Pass exactly one of order_id or line_item_id"}
    viewports = list(viewports or DEFAULT_VIEWPORTS)
    unknown = [v for v in viewports if v not in VIEWPORTS]
    if unknown:
        return {"error": f"Unknown viewports {unknown}; use {sorted(VIEWPORTS)}"}
    try:
        for url in [site_url, *(site_urls or {}).values()]:
            if url:
                validate_site_url(url)
        root = get_evidence_root()
    except (EvidenceError, OSError) as e:
        return {"error": str(e)}

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "error": "Screenshots need Playwright. Install it with: "
            "uv sync --extra screenshots && uv run playwright install chromium "
            "(Docker: build with --build-arg WITH_SCREENSHOTS=1)"
        }

    plan = await asyncio.to_thread(
        plan_captures, order_id, line_item_id, site_url, site_urls, network_code
    )
    if "error" in plan:
        return plan

    order, items = plan["order"], plan["items"]
    order_dir = root / str(int(order["id"]))
    capturable = [i for i in items if "preview_url" in i]
    total, done = len(capturable) * len(viewports), 0
    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    async with async_playwright() as p:
        try:
            # Docker's default 64 MB /dev/shm crashes Chromium on long pages
            browser = await p.chromium.launch(args=["--disable-dev-shm-usage"])
        except Exception as e:
            return {
                "error": f"Could not start Chromium ({e}). "
                "Install it with: uv run playwright install chromium"
            }
        try:
            for viewport in viewports:
                preset = VIEWPORTS[viewport]
                options = {**p.devices[preset["device"]], "viewport": preset["viewport"]}
                # One context per viewport, so an accepted consent banner stays accepted
                context = await browser.new_context(**options)
                try:
                    for item in capturable:
                        out_dir = order_dir / str(int(item["line_item_id"]))
                        out_dir.mkdir(parents=True, exist_ok=True)
                        capture = await _capture_one(context, item, viewport, out_dir)
                        item.setdefault("captures", []).append(capture)
                        done += 1
                        if on_progress:
                            await on_progress(done, total)
                finally:
                    await context.close()

            pdf_path = None
            if any(c.get("page_png") for i in capturable for c in i.get("captures", [])):
                page = await browser.new_page()
                try:
                    await page.set_content(_evidence_html(order, items, captured_at))
                    pdf_path = order_dir / "evidence.pdf"
                    await page.pdf(path=str(pdf_path), format="A4", print_background=True)
                finally:
                    await page.close()
        finally:
            await browser.close()

    for item in items:
        item.pop("preview_url", None)  # long signed URLs add noise, not information
    captures = [c for i in items for c in i.get("captures", [])]
    return {
        "order": order,
        "evidence_dir": str(order_dir),
        "pdf": str(pdf_path) if pdf_path else None,
        "summary": {
            "creatives": len(items),
            "skipped": len(items) - len(capturable),
            "captures": len(captures),
            "slot_found": sum(1 for c in captures if c.get("slot_found")),
            "failed": sum(1 for c in captures if c.get("error")),
        },
        "items": items,
    }
