# Google Ad Manager MCP Server

[![PyPI version](https://img.shields.io/pypi/v/google-ad-manager-mcp.svg)](https://pypi.org/project/google-ad-manager-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io/)

> **Automate Google Ad Manager with AI.** An MCP server that lets AI assistants like Claude, ChatGPT, Gemini, Cursor, and VS Code manage your ad campaigns, line items, creatives, and more through natural language.

<p align="center">
  <strong>Built by <a href="https://matious.com">Matious</a></strong> — We build custom AI tools and MCP servers for businesses.
</p>

---

## Why This Exists

Managing Google Ad Manager is tedious. Creating campaigns, uploading creatives, and configuring line items involves countless clicks through a complex UI.

**This MCP server changes that.** Connect it to Claude and manage your entire ad operations through conversation:

- *"Create a new campaign for Nike ending December 31st"*
- *"Upload all creatives from this folder and associate them with the Display line item"*
- *"Check which orders are currently delivering"*

No more clicking. Just tell Claude what you need.

## Features

- **Order Management**: List, create, and manage orders
- **Line Item Management**: Create, duplicate, and configure line items
- **Creative Management**: Upload images, associate with line items, bulk upload
- **Advertiser Management**: Find, create, and list advertisers
- **Verification Tools**: Verify line item setup, check delivery status
- **Campaign Workflow**: Complete campaign creation in one operation

## Installation

### From PyPI (Recommended)

```bash
pip install google-ad-manager-mcp
```

Or with uv:

```bash
uv pip install google-ad-manager-mcp
```

### From Source

```bash
git clone https://github.com/MatiousCorp/google-ad-manager-mcp.git
cd google-ad-manager-mcp
pip install -e .
```

### Dependencies

- **[FastMCP](https://github.com/jlowin/fastmcp)**: MCP server framework with native middleware support
- **[googleads](https://github.com/googleads/googleads-python-lib)**: Google Ad Manager SOAP API client

## Configuration

The server uses environment variables for configuration:

| Variable | Description | Required |
|----------|-------------|----------|
| `GAM_CREDENTIALS_PATH` | Path to service account JSON | **Yes** |
| `GAM_NETWORK_CODES` | Comma-separated list of GAM network codes (first is the default) | **Yes** |
| `GAM_MCP_TRANSPORT` | Transport mode: `stdio` or `http` | No (default: `stdio`) |
| `GAM_MCP_HOST` | Server host (HTTP mode only) | No (default: `127.0.0.1`) |
| `GAM_MCP_PORT` | Server port (HTTP mode only) | No (default: `8000`) |
| `GAM_MCP_AUTH_TOKEN` | Bearer token, at least 32 characters (HTTP mode only) | **Yes in HTTP mode** (the server refuses to start without it) |
| `GAM_UPLOAD_DIR` | Directory that upload tools may read creative images from | Only for upload tools |
| `GAM_API_VERSION` | Override the Ad Manager SOAP API version (default `v202608`) | No |
| `GAM_READ_ONLY` | `true` disables all tools that create or change anything (default `false`) | No |
| `GAM_EVIDENCE_SITE_URL` | Default page to preview creatives on (`get_preview_plan`) | No |

### Read-Only Mode

Set `GAM_READ_ONLY=true` to start with monitoring only. The 18 tools that create or change anything in Ad Manager (creating, updating, pausing, resuming, archiving and approving orders, line items, creatives and advertisers, uploads and `create_campaign`) are then neither listed nor callable; reading, verification, delivery progress and reports still work. Accepted values are `true/false`, `yes/no`, `on/off` and `1/0`; anything else stops the server from starting rather than silently allowing writes.

Read-only mode is a server-side guard, not a replacement for permissions: give a monitoring-only deployment a service account with a read-only Ad Manager role as well.

### Creative Uploads

Upload tools (`upload_creative`, `upload_and_associate_creative`, `bulk_upload_creatives`, `create_campaign`) only read files inside `GAM_UPLOAD_DIR`. Relative paths are resolved against that directory, symlinks that point outside it are refused, and only regular PNG, JPEG or GIF files up to 10 MB are accepted. Point it at a dedicated folder that holds nothing but creatives — never your home directory or a folder containing credentials.

### Evidence Screenshots

Agency evidence screenshots are taken on your own machine, not by the server. The server's read-only `get_preview_plan` tool lists every active creative of an order or line item with an Ad Manager preview URL that forces that creative into its slot, so rotating banners need no refreshing. The local `gam-evidence` command fetches that plan over MCP, opens each preview in headless Chromium (desktop 1440 px and mobile 390 px), accepts common cookie consent banners, scrolls until the slot that rendered the creative appears, and saves:

```
evidence/<order>/<line item>/<creative>_<viewport>.png       # full page
evidence/<order>/<line item>/<creative>_<viewport>_slot.png  # slot only
evidence/<order>/evidence.pdf                                # everything, one page per capture
evidence/<order>/summary.json                                # what was captured, also printed to stdout
```

Install the optional Playwright extra and a Chromium build once:

```bash
uv sync --extra screenshots
uv run playwright install chromium
```

Then point it at the running server (default `http://localhost:8080/mcp`, or `GAM_MCP_URL`) with the server's bearer token in `GAM_MCP_TOKEN`:

```bash
export GAM_MCP_TOKEN=...
uv run gam-evidence --order 123456789 --site-url https://www.example.com/news/...
uv run gam-evidence --line-item 987654321 \
  --site-url-for 300x600=https://www.example.com/a --site-url-for 970x250=https://www.example.com/b \
  --viewport desktop
```

The site URL must be a page that requests the same ad unit and size. Preview ignores key-values and geo targeting, and does not support SafeFrame or interstitial creatives. When a slot cannot be found the full page is still saved, with `diagnostics` in the summary: whether GPT loaded, whether the preview parameters survived redirects, and every slot on the page with its ad unit, sizes and rendered line item and creative. `--headed` shows the browser. If your DNS blocks Google's ad servers (an ad blocker), the command stops with a hint; `--public-dns` resolves just those hosts over DNS-over-HTTPS for its own browser. `evidence/` is git-ignored.

### Multi-Network Support

You can manage multiple GAM networks with a single server instance. List all network codes in `GAM_NETWORK_CODES` — the first one is the default:

```bash
export GAM_NETWORK_CODES="31083078,22706375620,98765432"
```

All tools accept an optional `network_code` parameter. When omitted, the first (default) network is used. The same service account credentials are shared across all networks — just ensure the service account email has been added as a user in each network.

For Claude Code MCP configuration:

```json
{
  "google-ad-manager": {
    "command": "uvx",
    "args": ["google-ad-manager-mcp"],
    "env": {
      "GAM_CREDENTIALS_PATH": "/path/to/credentials.json",
      "GAM_NETWORK_CODES": "31083078,22706375620"
    }
  }
}
```

## Authentication

The local **stdio** transport (the default) runs as a child process of your MCP client and needs no token. Prefer it for single-user use.

The **HTTP** transport always requires a bearer token:

- The server refuses to start in HTTP mode unless `GAM_MCP_AUTH_TOKEN` is set to at least 32 characters. Empty or whitespace values are rejected, so an unset secret in deployment config cannot silently disable authentication.
- Every HTTP request — including MCP initialization and session requests — is checked at the ASGI layer before FastMCP handles it; requests without a valid token get `401`.
- Tool calls are checked again by FastMCP middleware as defense in depth. Both checks fail closed and use constant-time comparison (`hmac.compare_digest`).
- The token is never written to logs. Provision it from a secret manager.
- The HTTP host defaults to `127.0.0.1`. Bind to other interfaces only behind TLS and network access controls.

All authenticated callers share the same service account and its Ad Manager permissions. There is no per-user or per-tool authorization, so give the service account the minimum role it needs.

### Setup

```bash
# Generate a secure token
python -c "import secrets; print(secrets.token_hex(32))"

# Set it as environment variable
export GAM_MCP_AUTH_TOKEN="your-generated-token"
export GAM_MCP_TRANSPORT=http
```

Clients must include the token in the Authorization header:
```
Authorization: Bearer your-generated-token
```

### Endpoints

| Endpoint | Description |
|----------|-------------|
| `/mcp` | MCP protocol endpoint (every request requires a bearer token in HTTP mode) |

## Running the Server

### Local Development

```bash
# Using the installed command
gam-mcp

# Or directly with Python
python -m gam_mcp.server

# HTTP mode on a custom port
GAM_NETWORK_CODES=12345678 GAM_MCP_TRANSPORT=http GAM_MCP_PORT=9000 \
  GAM_MCP_AUTH_TOKEN="your-generated-token" gam-mcp
```

### Docker Deployment

The image runs the server's native HTTP transport (no proxy) as an unprivileged user (UID `10001`). It contains only the installed virtualenv: no source checkout, build tools or credentials. The build context is restricted by `.dockerignore`, and dependencies are installed from the committed `uv.lock`.

Defaults inside the image: `GAM_MCP_TRANSPORT=http`, `GAM_MCP_HOST=0.0.0.0`, `GAM_MCP_PORT=8000`, `GAM_CREDENTIALS_PATH=/run/secrets/gam-credentials.json`, `GAM_UPLOAD_DIR=/uploads`.

Keep the service account key **outside** the repository. The mounted key must be readable by UID `10001` (for example `chown 10001 key.json && chmod 400 key.json` on Linux, or use Docker/Compose secrets).

#### Build the Image

```bash
docker build -t google-ad-manager-mcp .
```

The image has no browser: evidence screenshots run on your machine with `gam-evidence` (see [Evidence Screenshots](#evidence-screenshots)).

#### Run the Container

```bash
docker run -d \
  --name gam-mcp \
  -p 127.0.0.1:8000:8000 \
  --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 512m --pids-limit 128 \
  -v /secure/path/gam-key.json:/run/secrets/gam-credentials.json:ro \
  -v /path/to/creatives:/uploads:ro \
  -e GAM_NETWORK_CODES=YOUR_NETWORK_CODE \
  -e GAM_MCP_AUTH_TOKEN \
  google-ad-manager-mcp
```

`-e GAM_MCP_AUTH_TOKEN` without a value passes the token from your shell environment instead of putting it on the command line. The container exits immediately if the token is missing or shorter than 32 characters. Publish the port on `127.0.0.1` unless TLS and network access controls sit in front of it.

#### Docker Compose

```yaml
services:
  gam-mcp:
    build: .
    ports:
      - "127.0.0.1:8000:8000"
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    mem_limit: 512m
    pids_limit: 128
    volumes:
      - /path/to/creatives:/uploads:ro
    secrets:
      - gam-credentials.json
    environment:
      - GAM_NETWORK_CODES=YOUR_NETWORK_CODE
      - GAM_MCP_AUTH_TOKEN  # taken from the shell / .env, never committed
    restart: unless-stopped

secrets:
  gam-credentials.json:
    file: /secure/path/gam-key.json
```

#### Verify the Container

```bash
# Without a token: 401 Unauthorized
curl -i -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc": "2.0", "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, "id": 1}'

# With the token: 200 OK
curl -i -X POST http://localhost:8000/mcp \
  -H "Authorization: Bearer $GAM_MCP_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc": "2.0", "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, "id": 1}'
```

### Cloud Deployment (Railway, Fly.io, etc.)

1. Set environment variables in your cloud provider's secret store:
   - `GAM_CREDENTIALS_PATH`: Path where the platform mounts the key secret
   - `GAM_NETWORK_CODES`: Your Ad Manager network code(s)
   - `GAM_MCP_AUTH_TOKEN`: A random token of at least 32 characters

2. Deploy using the included Dockerfile behind TLS, and restrict who can reach the endpoint.

## Connecting to AI Assistants

### Claude Desktop (uvx - Recommended)

The easiest way to use this server with Claude Desktop. Pin the version you have reviewed (`google-ad-manager-mcp==X.Y.Z`) rather than running whatever is latest on PyPI. Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "google-ad-manager": {
      "command": "uvx",
      "args": ["google-ad-manager-mcp==0.1.14"],
      "env": {
        "GAM_CREDENTIALS_PATH": "/path/to/your/credentials.json",
        "GAM_NETWORK_CODES": "YOUR_NETWORK_CODE",
        "GAM_UPLOAD_DIR": "/path/to/creatives"
      }
    }
  }
}
```

### Claude Desktop (Docker)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "google-ad-manager": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "GAM_MCP_TRANSPORT=stdio",
        "-e", "GAM_NETWORK_CODES",
        "-v", "/secure/path/gam-key.json:/run/secrets/gam-credentials.json:ro",
        "-v", "/path/to/creatives:/uploads:ro",
        "google-ad-manager-mcp"
      ],
      "env": {
        "GAM_NETWORK_CODES": "YOUR_NETWORK_CODE"
      }
    }
  }
}
```

### HTTP Mode

HTTP mode always requires the bearer token. Use `https://` for anything other than localhost:

```json
{
  "mcpServers": {
    "google-ad-manager": {
      "url": "https://your-server.com/mcp",
      "headers": {
        "Authorization": "Bearer your-secure-token"
      }
    }
  }
}
```

### Other MCP Clients

This server works with any MCP-compatible client, including:

- **ChatGPT Desktop** - OpenAI adopted MCP in March 2025
- **Cursor** - AI-powered IDE with MCP support
- **VS Code** - Via MCP extensions
- **Windsurf, Zed, Codeium** - Various IDE integrations

Refer to each client's documentation for MCP server configuration.

### Testing with MCP Inspector

```bash
# Stdio (no token needed)
npx @modelcontextprotocol/inspector gam-mcp

# HTTP: set the header in the Inspector UI
# Header: Authorization
# Value: Bearer your-token
npx @modelcontextprotocol/inspector http://localhost:8000/mcp
```

## Available Tools

### Order Tools

| Tool | Description |
|------|-------------|
| `list_delivering_orders` | List all orders with delivering line items |
| `get_order` | Get order details by ID or name |
| `create_order` | Create a new order |
| `find_or_create_order` | Find existing or create new order |

### Line Item Tools

| Tool | Description |
|------|-------------|
| `get_line_item` | Get line item details |
| `create_line_item` | Create a new line item |
| `duplicate_line_item` | Duplicate an existing line item |
| `update_line_item` | Update line item properties (name, type, delivery rate, priority, cost, goal, end date) |
| `list_line_items_by_order` | List all line items for an order |
| `pause_line_item` | Pause a delivering line item |
| `resume_line_item` | Resume a paused line item |
| `archive_line_item` | Archive a line item |
| `approve_line_item` | Approve a line item (for approval workflows) |

### Creative Tools

| Tool | Description |
|------|-------------|
| `upload_creative` | Upload an image creative |
| `associate_creative_with_line_item` | Associate creative with line item |
| `upload_and_associate_creative` | Upload and associate in one step |
| `bulk_upload_creatives` | Upload all creatives from a folder |
| `get_creative` | Get creative details |
| `list_creatives_by_advertiser` | List creatives for an advertiser |
| `update_creative` | Update creative destination URL or name |
| `list_creatives_by_line_item` | List creatives associated with a line item |
| `create_third_party_creative` | Create HTML/JavaScript ad tag (DCM, custom HTML) |
| `get_creative_preview_url` | Generate preview URL to see creative on your site |

### Advertiser Tools

| Tool | Description |
|------|-------------|
| `find_advertiser` | Find advertiser by name |
| `get_advertiser` | Get advertiser details |
| `list_advertisers` | List all advertisers |
| `create_advertiser` | Create a new advertiser |
| `find_or_create_advertiser` | Find or create advertiser |

### Verification Tools

| Tool | Description |
|------|-------------|
| `verify_line_item_setup` | Verify line item configuration |
| `check_line_item_delivery_status` | Check delivery progress |
| `get_delivery_progress` | Actual vs expected delivery for delivering line items, flags those behind |
| `verify_order_setup` | Verify entire order setup |
| `get_preview_plan` | Preview URL per active creative of an order or line item, for the local `gam-evidence` screenshot command |

### Reporting Tools

| Tool | Description |
|------|-------------|
| `run_delivery_report` | Generate delivery report (impressions, clicks, CTR, revenue) |
| `run_inventory_report` | Generate inventory report (ad requests, fill rate) |
| `run_custom_report` | Generate custom report with specified dimensions and metrics |

### Workflow Tools

| Tool | Description |
|------|-------------|
| `create_campaign` | Complete campaign creation workflow |

## Example Usage with Claude

```
User: List all delivering orders

Claude: [Uses list_delivering_orders tool]
Here are the currently delivering orders:
1. Campaign IPhone 17 Pro 2025/2026 (ID: 123456)
   - Display line item: 45,000 impressions delivered

User: Create a new campaign for "ACME Corp" ending December 31, 2025

Claude: [Uses create_campaign tool]
I'll create the campaign with:
- Advertiser: ACME Corp
- Order: ACME Campaign 2025
- Line Item: Display
- End Date: December 31, 2025

Campaign created successfully!
- Order ID: 789012
- Line Item ID: 345678
- 4 creatives uploaded and associated
```

## Development

### Setup

```bash
# Clone the repository
git clone https://github.com/MatiousCorp/google-ad-manager-mcp.git
cd google-ad-manager-mcp

# Install with dev dependencies
pip install -e ".[dev]"
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=gam_mcp --cov-report=html

# Run specific test file
pytest tests/test_utils.py
```

### Code Quality

```bash
# Run linter
ruff check .

# Run linter with auto-fix
ruff check . --fix
```

## Roadmap

The following features are planned for future releases:

### Near-term

- [ ] **Ad Unit Management** - List, get, and create ad units with hierarchy support
- [ ] **Placement Management** - Manage inventory placements and targeting
- [ ] **Forecast & Availability** - Check inventory availability and forecast impressions
- [x] **Creative Preview Links** - Generate preview URLs for creative-line item combinations

### Medium-term

- [ ] **Advanced Targeting** - Geographic, device, daypart, and custom key-value targeting
- [x] **Reporting Tools** - Generate and retrieve performance reports
- [ ] **Bulk Operations** - Batch updates for line items, creatives, and targeting
- [ ] **HTML5/Video Creatives** - Support for rich media and video creative uploads

### Long-term

- [ ] **Audience Management** - Create and manage audience segments
- [ ] **User & Permissions** - Manage users, roles, and order assignments
- [ ] **Yield Management** - Configure yield groups and optimization
- [ ] **Custom Reporting** - Scheduled reports with export capabilities

### Community Requests

Have a feature request? [Open an issue](https://github.com/MatiousCorp/google-ad-manager-mcp/issues) to suggest new functionality.

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history.

## API Version

Uses Google Ad Manager SOAP API version `v202608` by default (supported until August 2027). Override it with `GAM_API_VERSION`; the server refuses to start with a version the installed `googleads` library does not support.

## License

MIT - see [LICENSE](LICENSE) for details.

---

## Need a Custom MCP Server?

This project is built and maintained by **[Matious](https://matious.com)**.

We specialize in building custom AI tools and MCP servers that integrate with your existing systems. Whether you need to connect Claude to your CRM, ERP, ad platforms, or internal tools — we can help.

**What we build:**
- Custom MCP servers for any API or platform
- AI-powered automation workflows
- Claude integrations for business operations

**Get in touch:** [matious.com](https://matious.com)
