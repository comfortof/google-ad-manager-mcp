#!/bin/bash

# Google Ad Manager MCP Server Startup Script

# Required environment variables (no defaults - must be set by user)
if [ -z "$GAM_CREDENTIALS_PATH" ]; then
    echo "Error: GAM_CREDENTIALS_PATH environment variable is required."
    echo "Set it to the path of your Google Ad Manager service account JSON file."
    echo ""
    echo "Example:"
    echo "  export GAM_CREDENTIALS_PATH=/path/to/your/credentials.json"
    exit 1
fi

if [ -z "$GAM_NETWORK_CODES" ] && [ -z "$GAM_NETWORK_CODE" ]; then
    echo "Error: GAM_NETWORK_CODES environment variable is required."
    echo "Set it to a comma-separated list of Google Ad Manager network codes."
    echo ""
    echo "Example:"
    echo "  export GAM_NETWORK_CODES=12345678"
    exit 1
fi

# Optional configuration with defaults
export GAM_MCP_HOST="${GAM_MCP_HOST:-127.0.0.1}"
export GAM_MCP_PORT="${GAM_MCP_PORT:-8000}"

echo "Starting Google Ad Manager MCP Server..."
echo "  Credentials: $GAM_CREDENTIALS_PATH"
echo "  Network Codes: ${GAM_NETWORK_CODES:-$GAM_NETWORK_CODE}"
echo "  Transport: ${GAM_MCP_TRANSPORT:-stdio}"
echo "  Host: $GAM_MCP_HOST"
echo "  Port: $GAM_MCP_PORT"
echo ""

# Run the server
python -m gam_mcp.server
