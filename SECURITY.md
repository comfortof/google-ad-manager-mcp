# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in GAM MCP Server, please report it responsibly.

### How to Report

**Please do NOT report security vulnerabilities through public GitHub issues.**

Instead, send a detailed report to: **youssef@matious.com**

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fixes (optional)

### Response Timeline

- **Initial Response**: Within 48 hours
- **Status Update**: Within 7 days
- **Resolution Target**: Within 30 days (depending on severity)

### What to Expect

1. Acknowledgment of your report
2. Assessment of the vulnerability
3. Development and testing of a fix
4. Coordinated disclosure (we'll credit you unless you prefer anonymity)

## Security Best Practices for Users

### Credential Management

- Never commit `credentials.json` or service account keys to version control, and keep them outside the checkout (the Docker build context is allowlisted by `.dockerignore`)
- Restrict key file permissions (`chmod 600`) and rotate keys periodically
- Provision `GAM_MCP_AUTH_TOKEN` from a secret manager; the server never logs it
- Rotate authentication tokens periodically

### Deployment

- Prefer the local stdio transport for single-user use
- HTTP mode requires `GAM_MCP_AUTH_TOKEN` (at least 32 characters) and refuses to start without it
- Use HTTPS and network access controls for remote deployments; the HTTP host defaults to `127.0.0.1`
- The Docker image runs as an unprivileged user (UID 10001); also run it with `--read-only`, `--cap-drop ALL`, `no-new-privileges` and resource limits
- Point `GAM_UPLOAD_DIR` at a dedicated folder containing only creatives
- Pin the package version and build from the committed `uv.lock`

### Google Ad Manager Permissions

- Use service accounts with minimal required permissions; start with a read-only role
- Set `GAM_READ_ONLY=true` for monitoring-only deployments; write tools are then neither listed nor callable
- All authenticated callers share the service account's permissions: there is no per-user authorization
- Require human approval for write operations (pausing, archiving, pricing, goals, destinations, creatives)
- Regularly audit service account access
- Revoke unused service account keys

## Security Features

This project implements several security measures:

- **Bearer Token Authentication**: In HTTP mode every request, including initialization, requires a valid token; authentication fails closed
- **Constant-Time Comparison**: Prevents timing attacks on token validation
- **Parameterized Queries**: All GAM API queries use bind variables to prevent injection
- **Read-Only Mode**: `GAM_READ_ONLY=true` disables every tool that changes Ad Manager; unrecognised values refuse to start
- **No Server-Side Browser**: the server only returns preview URLs (`get_preview_plan`); screenshots run on the caller's machine with `gam-evidence`, which keeps the GAM key on the server and opens only public `http(s)` host names (no IP addresses, `localhost` or `.local`/`.internal` hosts), writing under its output directory with numeric IDs as path names
- **Upload Sandbox**: Upload tools only read regular image files inside `GAM_UPLOAD_DIR`, with size limits and image validation
- **Non-Root Docker**: Container runs as an unprivileged user with no source tree or build tools
- **Audit Logging**: Failed authentication attempts are logged (tokens are never logged)

## Acknowledgments

We thank the security researchers who help keep this project secure.
