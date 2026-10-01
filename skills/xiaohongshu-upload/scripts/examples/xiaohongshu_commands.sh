#!/usr/bin/env bash
set -euo pipefail
# Forward one explicit action to the preview-first Python template.
# Example: ./this-file.sh capabilities --db /path/to/tasks.sqlite3
exec uv run --frozen --offline --no-sync python "$(dirname "$0")/xiaohongshu_cli_template.py" "$@"
