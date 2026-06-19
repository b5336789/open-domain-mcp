#!/usr/bin/env bash
#
# Local launcher for open-domain-mcp.
# Loads .env into the environment (so ANTHROPIC_API_KEY reaches the SDK) and
# dispatches to the right entry point inside the project venv.
#
# Usage:
#   ./run.sh web                       # web dashboard (default) -> http://127.0.0.1:8000
#   ./run.sh server                    # MCP server over stdio
#   ./run.sh ingest ./path [--sync]    # ingest a file or directory
#   ./run.sh search "query" --top-k 5  # hybrid search
#   ./run.sh ask "question"            # cited answer (needs API credits)
#   ./run.sh stats | collections | clear
#
# Any subcommand other than web/server is passed straight to the `opendomainmcp` CLI.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
ENV_FILE="$ROOT/.env"

if [[ ! -d "$VENV" ]]; then
  echo "error: venv not found at $VENV — run: uv venv --python 3.11 .venv && uv pip install --python .venv -e '.[dev]'" >&2
  exit 1
fi

# Load .env so credentials (ANTHROPIC_API_KEY, etc.) are exported to the process.
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "warning: $ENV_FILE not found — continuing without it (copy .env.example to .env)" >&2
fi

cmd="${1:-web}"
shift || true

case "$cmd" in
  web)
    exec "$VENV/bin/opendomainmcp-web" "$@"
    ;;
  server)
    exec "$VENV/bin/opendomainmcp-server" "$@"
    ;;
  *)
    exec "$VENV/bin/opendomainmcp" "$cmd" "$@"
    ;;
esac
