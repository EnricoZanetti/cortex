#!/usr/bin/env bash
#
# Smoke-test the MCP endpoint end to end: rejects unauthenticated requests, completes the
# Streamable HTTP handshake, lists tools, and calls one for real.
#
# Usage:  ./scripts/check_mcp.sh [url] [api-key]
# Defaults come from .env if present.

set -euo pipefail

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a && source .env && set +a
fi

URL="${1:-http://localhost:${MCP_PORT:-8080}${MCP_PATH:-/mcp}}"
KEY="${2:-${MCP_API_KEY:-}}"

if [[ -z "$KEY" ]]; then
  echo "No API key. Pass it as the second argument or set MCP_API_KEY in .env." >&2
  exit 1
fi

call() {
  curl -sS -X POST "$URL" \
    -H "Authorization: Bearer $KEY" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d "$1" | grep '^data: ' | sed 's/^data: //'
}

echo "Endpoint: $URL"

echo -n "1. rejects a request with no credentials ... "
code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$URL" -H 'Content-Type: application/json' -d '{}')
[[ "$code" == "401" ]] && echo "OK (401)" || { echo "FAIL (got $code, expected 401)"; exit 1; }

echo -n "2. rejects a request with a wrong key ... "
code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$URL" \
  -H 'Authorization: Bearer definitely-not-the-key' -H 'Content-Type: application/json' -d '{}')
[[ "$code" == "401" ]] && echo "OK (401)" || { echo "FAIL (got $code, expected 401)"; exit 1; }

echo -n "3. initialize handshake ... "
call '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check_mcp","version":"1"}}}' \
  | grep -q '"serverInfo"\|"capabilities"' && echo "OK" || { echo "FAIL"; exit 1; }

read -r -d '' SHOW_TOOLS <<'PYTHON' || true
import json, sys
tools = json.load(sys.stdin)["result"]["tools"]
for tool in tools:
    required = ",".join(tool["inputSchema"].get("required", [])) or "-"
    print("     {:24} required: {}".format(tool["name"], required))
print("     ({} tools)".format(len(tools)))
PYTHON

read -r -d '' SHOW_RESULTS <<'PYTHON' || true
import json, sys
data = json.load(sys.stdin)["result"].get("structuredContent", {})
print("     scope: {}  results: {}".format(data.get("scope"), data.get("result_count")))
for hit in data.get("results", []):
    print("     {:.2f}  {}  |  {}".format(hit["relevance"], hit["filename"], hit.get("heading_path")))
PYTHON

echo "4. tools/list:"
call '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python3 -c "$SHOW_TOOLS"

echo "5. tools/call search:"
call '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search","arguments":{"query":"how quickly must a suspicious transaction be reported?","top_k":2,"max_chars_per_result":200}}}' \
  | python3 -c "$SHOW_RESULTS"

echo
echo "All checks passed."
