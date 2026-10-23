"""gam-evidence: take agency evidence screenshots on your own machine.

Asks the GAM MCP server for a preview plan (one preview URL per active
creative, via its read-only get_preview_plan tool), then opens each preview in
local headless Chromium. Each creative is forced into its slot, so there is no
refreshing to catch a rotating banner. The GAM key stays on the server.

Needs the optional `screenshots` extra (Playwright) and a Chromium build:

    uv sync --extra screenshots && uv run playwright install chromium
    uv run gam-evidence --order 123456789 --site-url https://www.example.com/...
"""

import argparse
import asyncio
import base64
import html
import ipaddress
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .tools.previews import EvidenceError, validate_site_url

SERVER_URL_ENV = "GAM_MCP_URL"
TOKEN_ENV = "GAM_MCP_TOKEN"
DEFAULT_SERVER_URL = "http://localhost:8080/mcp"
DEFAULT_OUT_DIR = "evidence"

# Google's ad hosts: if DNS blocks these, no creative can render. With
# --public-dns they are resolved over DNS-over-HTTPS for this browser only.
AD_HOST_RULES = {
    "*.doubleclick.net": "securepubads.g.doubleclick.net",
    "*.googlesyndication.com": "tpc.googlesyndication.com",
    "*.googletagservices.com": "www.googletagservices.com",
    "*.2mdn.net": "s0.2mdn.net",
}
DOH_URL = "https://dns.google/resolve"

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

# What GPT did on the page, reported when the creative's slot is not found
SLOT_DIAGNOSTICS_JS = """
() => {
  const gt = window.googletag;
  const out = {
    gpt_loaded: !!(gt && gt.apiReady),
    preview_in_url: /[?&]google_preview=/.test(location.search),
    final_url: location.origin + location.pathname,
    slots: [],
  };
  if (!gt || typeof gt.pubads !== 'function') return out;
  for (const slot of gt.pubads().getSlots()) {
    const info = slot.getResponseInformation();
    out.slots.push({
      ad_unit: slot.getAdUnitPath ? slot.getAdUnitPath() : null,
      sizes: slot.getSizes ? slot.getSizes().filter(z => z.getWidth)
        .map(z => z.getWidth() + 'x' + z.getHeight()) : [],
      line_item_id: info ? info.lineItemId : null,
      creative_id: info ? info.creativeId : null,
    });
  }
  return out;
}
"""
MAX_DIAGNOSTIC_SLOTS = 40


ProgressCallback = Callable[[int, int], Awaitable[None]]


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


async def _slot_diagnostics(page, size: Optional[str]) -> dict:
    """Summarise the page's GPT slots, to explain why the creative was not found."""
    try:
        diag = await page.evaluate(SLOT_DIAGNOSTICS_JS)
    except Exception as e:
        return {"error": str(e)}
    slots = diag.pop("slots")
    diag["slots_total"] = len(slots)
    diag["slots_rendered"] = sum(1 for s in slots if s["creative_id"])
    # Slots that could take this size come first: those are the candidates
    slots.sort(key=lambda s: size not in s["sizes"])
    diag["slots_with_size"] = sum(1 for s in slots if size in s["sizes"])
    diag["slots"] = slots[:MAX_DIAGNOSTIC_SLOTS]
    return diag


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
            result["diagnostics"] = await _slot_diagnostics(page, item["size"])
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




def ad_hosts_blocked() -> bool:
    """True when local DNS sinkholes Google's ad server, as ad blockers do."""
    try:
        infos = socket.getaddrinfo(AD_HOST_RULES["*.doubleclick.net"], 443)
    except OSError:
        return True
    addresses = {ipaddress.ip_address(info[4][0]) for info in infos}
    return all(a.is_unspecified or a.is_loopback for a in addresses)


def public_dns_rules() -> str:
    """Chromium --host-resolver-rules mapping ad hosts to public DNS answers."""
    import httpx

    rules = []
    for pattern, host in AD_HOST_RULES.items():
        response = httpx.get(DOH_URL, params={"name": host, "type": "A"}, timeout=10)
        response.raise_for_status()
        answers = [a["data"] for a in response.json().get("Answer", []) if a.get("type") == 1]
        if not answers:
            raise EvidenceError(f"Public DNS has no address for {host}")
        rules.append(f"MAP {pattern} {answers[0]}")
    return ", ".join(rules)


async def fetch_plan(server_url: str, token: Optional[str], arguments: dict) -> dict:
    """Call the server's get_preview_plan tool."""
    from fastmcp import Client

    async with Client(server_url, auth=token or None, timeout=300) as client:
        result = await client.call_tool("get_preview_plan", arguments)
    return json.loads(result.content[0].text)


async def capture(
    plan: dict,
    out_root: Path,
    viewports: Optional[list] = None,
    headed: bool = False,
    resolver_rules: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> dict:
    """Screenshot every previewable creative of a plan.

    Saves <out_root>/<order>/<line item>/<creative>_<viewport>.png (full page)
    and <creative>_<viewport>_slot.png (slot only), plus
    <out_root>/<order>/evidence.pdf.

    Returns:
        dict with the files written per creative and viewport
    """
    from playwright.async_api import async_playwright

    viewports = list(viewports or DEFAULT_VIEWPORTS)
    order, items = plan["order"], plan["items"]
    order_dir = out_root.resolve() / str(int(order["id"]))
    capturable = []
    for item in items:
        if "preview_url" not in item:
            continue
        try:
            validate_site_url(item["preview_url"])
            capturable.append(item)
        except EvidenceError as e:
            item["error"] = str(e)
    total, done = len(capturable) * len(viewports), 0
    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    args = ["--disable-dev-shm-usage"]
    if resolver_rules:
        args.append(f"--host-resolver-rules={resolver_rules}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headed, args=args)
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
                        result = await _capture_one(context, item, viewport, out_dir)
                        item.setdefault("captures", []).append(result)
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


def _size_url(value: str) -> tuple[str, str]:
    size, sep, url = value.partition("=")
    if not sep or "x" not in size:
        raise argparse.ArgumentTypeError(f"expected SIZE=URL, e.g. 300x250=https://...: {value}")
    return size.strip(), url.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gam-evidence",
        description="Take agency evidence screenshots of a GAM order or line item on real pages.",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--order", type=int, help="capture every line item of this order")
    target.add_argument("--line-item", type=int, help="capture a single line item")
    parser.add_argument("--site-url", help="page carrying the ad unit, for every size not in --site-url-for")
    parser.add_argument(
        "--site-url-for", type=_size_url, action="append", default=[], metavar="SIZE=URL",
        help="page for one creative size, e.g. 300x250=https://...; repeatable",
    )
    parser.add_argument(
        "--viewport", choices=sorted(VIEWPORTS), action="append",
        help="desktop (1440 px) or mobile (390 px); repeatable, default both",
    )
    parser.add_argument("--out", default=DEFAULT_OUT_DIR, help=f"output directory (default: {DEFAULT_OUT_DIR})")
    parser.add_argument("--network-code", help="GAM network code, if not the server's default")
    parser.add_argument(
        "--server", default=os.environ.get(SERVER_URL_ENV) or DEFAULT_SERVER_URL,
        help=f"MCP server URL (default: ${SERVER_URL_ENV} or {DEFAULT_SERVER_URL})",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument(
        "--public-dns", action="store_true",
        help="resolve Google's ad hosts over DNS-over-HTTPS, for networks whose DNS blocks ads",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    def log(message: str) -> None:
        print(message, file=sys.stderr)

    try:
        import playwright  # noqa: F401
    except ImportError:
        log("Screenshots need Playwright: uv sync --extra screenshots && uv run playwright install chromium")
        return 1

    resolver_rules = None
    if args.public_dns:
        resolver_rules = public_dns_rules()
    elif ad_hosts_blocked():
        log(
            "Your DNS blocks Google's ad servers (an ad blocker?), so no creative can render. "
            "Allow doubleclick.net and googlesyndication.com, or rerun with --public-dns."
        )
        return 1

    arguments = {
        "order_id": args.order,
        "line_item_id": args.line_item,
        "site_url": args.site_url,
        "site_urls": dict(args.site_url_for) or None,
        "network_code": args.network_code,
    }
    log(f"Fetching preview plan from {args.server} ...")
    try:
        plan = await fetch_plan(
            args.server, os.environ.get(TOKEN_ENV), {k: v for k, v in arguments.items() if v}
        )
    except Exception as e:
        log(
            f"Could not get the preview plan from {args.server}: {e}. "
            f"Check the server (or its proxy) is running and {TOKEN_ENV} holds its bearer token."
        )
        return 1
    if "error" in plan:
        log(plan["error"])
        return 1
    for item in plan["items"]:
        if "error" in item:
            log(f"Skipping creative {item['creative_id']}: {item['error']}")

    async def progress(done: int, total: int) -> None:
        log(f"Captured {done}/{total}")

    result = await capture(
        plan, Path(args.out), args.viewport, args.headed, resolver_rules, progress,
    )
    summary_path = Path(result["evidence_dir"]) / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0


def main(argv: Optional[list] = None) -> None:
    """Entry point for the gam-evidence command."""
    args = build_parser().parse_args(argv)
    try:
        sys.exit(asyncio.run(_run(args)))
    except Exception as e:
        print(f"gam-evidence: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
