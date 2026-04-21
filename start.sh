#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PORT=5000

cleanup() {
  echo ""
  echo "正在关闭服务器…"
  if [ -n "${SERVER_PID:-}" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  # Ensure port is released
  lsof -ti:"$PORT" 2>/dev/null | xargs kill 2>/dev/null || true
  echo "服务器已停止。"
}

trap cleanup EXIT INT TERM HUP

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

uv sync

# Free port if occupied by a previous run
lsof -ti:"$PORT" 2>/dev/null | xargs kill 2>/dev/null || true

uv run loanratio &
SERVER_PID=$!
echo "服务器已启动 (PID: $SERVER_PID, 端口: $PORT)"
wait "$SERVER_PID"
