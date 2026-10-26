---
name: gam-evidence
description: Take agency evidence screenshots of a Google Ad Manager order or line item on real site pages (PNG per creative and viewport plus one PDF). Use when the user asks for campaign evidence, proof-of-placement screenshots, or screenshots of an order's / line item's banners.
---

# GAM evidence screenshots

Screenshots are taken on this machine by the `gam-evidence` command. It asks
the GAM MCP server (`get_preview_plan` tool) for one preview URL per active
creative, then opens each preview in headless Chromium, where that creative is
forced into its slot. The server is read-only for this; nothing in Ad Manager
changes.

## 1. Gather the inputs

- **Order ID or line item ID.** If the user gives a name, find the ID with the
  google-ad-manager MCP tools (`get_order` with `order_name`,
  `list_delivering_orders`, `list_line_items_by_order`).
- **Site page per creative size.** The page must request the same ad unit and
  size as the line item, typically an article in the section the campaign
  targets. `get_line_item_inventory` shows which ad unit paths the line item
  targets, and `get_ad_unit_pages` (by SITE or URL) where those units served.
  Ask the user for it if they did not give one; do not guess. Use
  `--site-url` for one page, or `--site-url-for SIZE=URL` per size.
- **Viewports:** desktop (1440 px) and mobile (390 px) by default; pass
  `--viewport desktop` or `--viewport mobile` to limit.

## 2. Check the prerequisites

Run from the repository root. `GAM_MCP_PROJECT` and `GAM_MCP_REGION` name
the Google Cloud project and region of the `gam-mcp` Cloud Run service; if
they are not set, read them from `.pi/mcp.json` (local, not committed) or
ask the user.

1. The MCP server proxy must answer on port 8080:
   `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/mcp`
   prints a 4xx code when it is up, `000` when it is down. If down, ask the
   user to start it in their own terminal:
   `gcloud run services proxy gam-mcp --region "$GAM_MCP_REGION" --project "$GAM_MCP_PROJECT" --port 8080`
2. Playwright and Chromium, once per machine:
   `uv sync --extra screenshots && uv run playwright install chromium`

## 3. Run it

```bash
GAM_MCP_TOKEN="$(gcloud secrets versions access latest --secret gam-mcp-auth-token --project "$GAM_MCP_PROJECT")" \
  uv run gam-evidence --line-item <LINE_ITEM_ID> --site-url 'https://www.example.com/...'
```

- Never print or echo the token.
- If it stops with "Your DNS blocks Google's ad servers", rerun with
  `--public-dns`.
- It takes several seconds per creative and viewport; progress goes to stderr,
  the JSON summary to stdout and `evidence/<order>/summary.json`.

## 4. Check the result

For each capture in the summary:

- `slot_found: true`: open the `slot_png` image and confirm the banner is
  visible and is the expected creative before calling it evidence.
- `slot_found: false`: only the full page was saved. Read `diagnostics`:

| Diagnostic | Meaning | Next step |
| --- | --- | --- |
| `gpt_loaded: false` | Google's ad script did not run | DNS or ad blocker: rerun with `--public-dns` |
| `preview_in_url: false` | The page redirected and dropped the preview parameters | Use the final URL (`final_url`) as the site URL |
| `slots_with_size: 0` | No slot on that page takes this size | Ask for a page that carries this size |
| slots with the size, none showing the creative | Wrong ad unit for this line item | Pick a page from the section the line item targets; `slots[].ad_unit` lists what the page has |

`--headed` shows the browser if the user wants to watch a run.

## 5. Report

Tell the user the PDF path (`pdf`), how many captures found their slot
(`summary.slot_found` of `summary.captures`), and any skipped creatives with
their error. Files stay under `evidence/`, which is git-ignored client data:
never commit or upload them unless asked.
