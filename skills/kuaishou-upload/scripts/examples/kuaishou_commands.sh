#!/usr/bin/env bash
set -euo pipefail
# Preview one operation; --execute opts in. Login for Bilibili requires a real terminal.
exec uv run --frozen --offline --no-sync python "$(dirname "$0")/kuaishou_cli_template.py" "$@"
