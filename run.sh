#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAILY_DIR="$SCRIPT_DIR/wechat-daily"

if [[ "${1:-}" == "--cli" ]]; then
  shift
  exec "$DAILY_DIR/run_group_daily.sh" "$@"
fi

DEFAULT_CONFIG="$DAILY_DIR/config.yaml"
if [[ -f "$DAILY_DIR/config.local.yaml" ]]; then
  DEFAULT_CONFIG="$DAILY_DIR/config.local.yaml"
fi
CONFIG_PATH="${1:-$DEFAULT_CONFIG}"

PYTHON="$DAILY_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi

if [[ "${1:-}" == --* ]]; then
  exec "$PYTHON" "$DAILY_DIR/web_ui.py" "$@"
fi

exec "$PYTHON" "$DAILY_DIR/web_ui.py" --config "$CONFIG_PATH"
