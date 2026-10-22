# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `capture_campaign_screenshots` tool: agency evidence screenshots of every active creative of an order or line item, forced into its slot via preview URL in headless Chromium (optional `screenshots` extra); saves full-page and slot-only PNGs per desktop/mobile viewport plus one combined PDF. Docker images include it when built with `--build-arg WITH_SCREENSHOTS=1`
- `GAM_READ_ONLY=true` read-only mode: the 18 tools that create or change anything are tagged `write` and are neither listed nor callable; unrecognised values stop the server from starting
- `get_delivery_progress` tool: actual vs expected delivery (GAM's delivery indicator, as in the UI "Progress" bar) for delivering line items of an order or the whole network, flagging those behind by a configurable number of points

### Changed

- Upgraded FastMCP from 2.14 to 4.1 (MCP SDK 2.3); this drops the `diskcache` dependency and resolves the FastMCP and diskcache advisories from the security review
- Upgraded `googleads` from 48 to 51 and moved the default Ad Manager SOAP API version to `v202608` (sunset August 2027); the previous default `v202602` was not supported by `googleads` 48
- The server now refuses to start when `GAM_API_VERSION` names a version the installed `googleads` library does not support

### Security

- HTTP transport now requires `GAM_MCP_AUTH_TOKEN` (at least 32 characters) and refuses to start without it; empty tokens no longer disable authentication
- Every HTTP request, including initialization, is authenticated at the ASGI layer; tool-level checks fail closed
- Tool-level authentication reads the `Authorization` header explicitly, which FastMCP 4 strips from its default header helper
- The auth token is no longer written to logs; the HTTP host defaults to `127.0.0.1`
- Upload tools only read regular PNG/JPEG/GIF files up to 10 MB inside the new `GAM_UPLOAD_DIR`; symlinks escaping it are refused
- Docker image rebuilt: native authenticated HTTP instead of an unauthenticated `mcp-proxy`, non-root user, allowlisted build context, locked dependencies
- `secrets/` and key files are git-ignored; `uv.lock` is now committed

### Fixed

- `create_campaign` no longer reports success when creative uploads or associations fail, and validates the creatives folder before creating anything
- Association failures in `upload_and_associate_creative` are reported as errors, fixing a `KeyError` in `bulk_upload_creatives`
- `update_line_item` works with real Zeep responses, and a successful update is no longer reported as a failure
- Order verification follows pagination and reports `INCOMPLETE` when a line item cannot be verified
- Line item end dates use the network's configured time zone instead of `Africa/Casablanca`
- `check_line_item_delivery_status` reports GAM's `delivery_indicator` instead of the mislabelled `is_set_to_deliver` (which was `isSetTopBoxEnabled`)

## [0.1.13] - 2026-03-10

### Added

- **Multi-network support**: Manage multiple GAM networks with a single server instance
  - New `GAM_NETWORK_CODES` environment variable: comma-separated list of network codes (first is default)
  - All tools accept an optional `network_code` parameter to target a specific network
  - Same service account credentials shared across all networks
  - Clients are lazily created and cached per network code
  - Backward compatible: `GAM_NETWORK_CODE` still works for single-network setups

### Changed

- Upgrade Google Ad Manager API version from `v202411` to `v202502` (v202411 was sunset)

## [0.1.11] - 2025-01-16

### Added

- **`get_line_item`** now returns `targeted_ad_unit_ids` - List of ad unit IDs targeted by the line item
- **`update_line_item`** - Comprehensive line item update tool with support for:
  - `name` - Update line item name
  - `line_item_type` - Change type (STANDARD, SPONSORSHIP, NETWORK, BULK, PRICE_PRIORITY, HOUSE)
  - `delivery_rate_type` - Change delivery pacing (EVENLY, FRONTLOADED, AS_FAST_AS_POSSIBLE)
  - `priority` - Set priority value (1-16)
  - `cost_per_unit_micro` - Update CPM/CPC cost
  - `currency_code` - Change currency
  - `goal_impressions` - Update impression goal
  - `end_year`, `end_month`, `end_day` - Update end date
  - Returns a list of all changes made

### Removed

- **`update_line_item_name`** - Superseded by the more comprehensive `update_line_item` tool

## [0.1.10] - 2024-12-24

### Added

- **Creative Preview URL** - Generate preview URLs to see how creatives will appear on your site:
  - `get_creative_preview_url` - Get a preview URL for a creative associated with a line item
  - Takes `line_item_id`, `creative_id`, and `site_url` as parameters
  - Returns a URL that loads the site with the creative displayed in ad slots
  - Uses the official GAM SOAP API `LineItemCreativeAssociationService.getPreviewUrl` method

## [0.1.9] - 2024-12-24

### Added

- **Third-Party Creative Support** - Create HTML/JavaScript ad creatives programmatically:
  - `create_third_party_creative` - Create DCM/Campaign Manager tags, custom HTML ads, or any third-party ad server tags
  - Supports SafeFrame compatibility setting
  - Supports expanded snippets for expandable creatives
  - Use with existing `associate_creative_with_line_item` to link to line items

## [0.1.8] - 2024-12-24

### Added

- **`update_creative`** - Update an existing creative's properties:
  - `destination_url` - Change the click-through URL
  - `name` - Rename the creative
- **`list_creatives_by_line_item`** - List all creatives associated with a specific line item:
  - Returns creative details including name, size, type, destination URL
  - Includes association status (ACTIVE, INACTIVE, etc.)

## [0.1.7] - 2025-12-23

### Added

- **Delivery metrics in `list_delivering_orders`** - Now includes full delivery tracking for each line item:
  - `goal_type` - Goal type (LIFETIME, DAILY, etc.)
  - `goal_unit_type` - Unit type (IMPRESSIONS, CLICKS, etc.)
  - `goal_units` - Target goal units
  - `progress_percent` - Actual delivery vs goal percentage
  - `expected_delivery` - Expected impressions based on time elapsed
  - `pacing_percent` - Actual vs expected delivery (100% = on track)
  - `days_elapsed` / `total_days` - Campaign time progress

### Fixed

- Fixed `safe_get` utility to properly handle zeep objects that incorrectly pass `isinstance(obj, dict)` but lack `.get()` method
- Changed from `isinstance(obj, dict)` to `type(obj) is dict` check to avoid zeep object issues

## [0.1.6] - 2025-12-23

### Added

- **Line Item Status Control** - New tools to manage line item lifecycle:
  - `pause_line_item` - Pause a delivering line item to stop ad delivery
  - `resume_line_item` - Resume a paused line item to restart delivery
  - `archive_line_item` - Archive a line item (hides from UI, cannot be undone via API)
  - `approve_line_item` - Approve a line item in approval workflow (NEEDS_APPROVAL status)
- **Reporting Tools** - New tools to generate and retrieve performance reports:
  - `run_delivery_report` - Generate delivery report with impressions, clicks, CTR, and revenue by order/line item
  - `run_inventory_report` - Generate inventory report with ad requests, impressions, and fill rate by ad unit
  - `run_custom_report` - Generate custom report with user-specified dimensions and metrics
  - Supports multiple date ranges: TODAY, YESTERDAY, LAST_WEEK, LAST_MONTH, LAST_3_MONTHS, CUSTOM_DATE
  - Filtering by order ID, line item ID, or ad unit ID
  - Optional daily/weekly/monthly breakdown
- **Pacing Calculation** - Enhanced `check_line_item_delivery_status` with pacing metrics:
  - `pacing_percent` - Actual vs expected delivery based on time elapsed (100% = on track)
  - `expected_delivery` - Impressions that should be delivered by now
  - `days_elapsed` / `total_days` - Time progress through the campaign

### Fixed

- Fixed `check_line_item_delivery_status` using `.get()` on zeep objects instead of `safe_get()`

## [0.1.5] - 2025-12-22

### Added

- Added `creative_sizes` parameter to `create_line_item` tool - allows specifying multiple creative sizes as JSON
- Added `cost_per_unit_micro` and `currency_code` parameters to `create_line_item` tool
- Added `line_item_type` and `creative_sizes` parameters to `create_campaign` workflow tool
- Documented all supported line item types: SPONSORSHIP, STANDARD, NETWORK, BULK, PRICE_PRIORITY, HOUSE, CLICK_TRACKING, ADSENSE, AD_EXCHANGE, BUMPER, PREFERRED_DEAL

### Changed

- Enhanced `create_line_item` documentation with detailed descriptions of all line item types

## [0.1.4] - 2025-12-22

### Fixed

- Fixed RuntimeError when calling tools: `init_client()` was incorrectly using `get_gam_client()` which throws an error when client is not initialized
- Added `is_gam_client_initialized()` helper function to properly check initialization state

## [0.1.3] - 2025-12-19

### Fixed

- Fixed lazy initialization to allow server to start and list tools without credentials
- Credentials are now only validated when a tool is actually called
- Fixed duplicate `init_client()` calls in `get_order` function
- Added missing `init_client()` call to `create_campaign` function

### Changed

- Default transport mode changed from `http` to `stdio` for better CLI/uvx compatibility
- Updated tests to support lazy initialization behavior

## [0.1.2] - 2025-12-19

### Fixed

- Changed default transport from `http` to `stdio` to fix uvx compatibility

## [0.1.1] - 2025-12-19

### Added

- Added `google-ad-manager-mcp` as alternate executable name for uvx compatibility

## [0.1.0] - 2025-12-19

### Added

- Initial release of GAM MCP Server
- **Order Management**
  - `list_delivering_orders` - List all orders with delivering line items
  - `get_order` - Get order details by ID or name
  - `create_order` - Create a new order
  - `find_or_create_order` - Find existing or create new order (idempotent)
- **Line Item Management**
  - `get_line_item` - Get line item details
  - `create_line_item` - Create a new line item with customizable sizes, dates, impressions
  - `duplicate_line_item` - Duplicate an existing line item with optional source rename
  - `update_line_item_name` - Rename a line item
  - `list_line_items_by_order` - List all line items for an order
- **Creative Management**
  - `upload_creative` - Upload an image creative (auto-extracts size from filename)
  - `associate_creative_with_line_item` - Associate creative with line item
  - `upload_and_associate_creative` - Upload and associate in one operation
  - `bulk_upload_creatives` - Batch upload all creatives from a folder
  - `get_creative` - Get creative details
  - `list_creatives_by_advertiser` - List creatives for an advertiser with pagination
- **Advertiser Management**
  - `find_advertiser` - Find advertiser by partial name match
  - `get_advertiser` - Get advertiser details by ID
  - `list_advertisers` - List all advertisers with pagination
  - `create_advertiser` - Create a new advertiser
  - `find_or_create_advertiser` - Find or create advertiser (idempotent)
- **Verification Tools**
  - `verify_line_item_setup` - Validate creative placeholders, associations, size mismatches
  - `check_line_item_delivery_status` - Track impressions/clicks vs goals
  - `verify_order_setup` - Comprehensive order validation
- **Workflow Tools**
  - `create_campaign` - End-to-end campaign creation (advertiser → order → line item → creatives)
- **Security Features**
  - Bearer token authentication with FastMCP middleware
  - Cryptographically secure token generation
  - Timing attack prevention with constant-time comparison
- **Infrastructure**
  - Docker support with non-root user
  - Environment-based configuration
  - Comprehensive logging

[Unreleased]: https://github.com/MatiousCorp/google-ad-manager-mcp/compare/v0.1.11...HEAD
[0.1.11]: https://github.com/MatiousCorp/google-ad-manager-mcp/compare/v0.1.10...v0.1.11
[0.1.10]: https://github.com/MatiousCorp/google-ad-manager-mcp/compare/v0.1.9...v0.1.10
[0.1.9]: https://github.com/MatiousCorp/google-ad-manager-mcp/compare/v0.1.8...v0.1.9
[0.1.8]: https://github.com/MatiousCorp/google-ad-manager-mcp/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/MatiousCorp/google-ad-manager-mcp/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/MatiousCorp/google-ad-manager-mcp/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/MatiousCorp/google-ad-manager-mcp/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/MatiousCorp/google-ad-manager-mcp/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/MatiousCorp/google-ad-manager-mcp/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/MatiousCorp/google-ad-manager-mcp/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/MatiousCorp/google-ad-manager-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/MatiousCorp/google-ad-manager-mcp/releases/tag/v0.1.0
