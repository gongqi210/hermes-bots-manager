#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8710}"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${FRONTEND_PORT:-5710}"

RUN_INSTALL="auto"
RUN_MIGRATIONS=1
OPEN_BROWSER=1
PIDS=()

usage() {
  cat <<USAGE
Usage: ./start.sh [options]

Starts Hermes Bots Manager locally:
  backend  http://localhost:${BACKEND_PORT}
  frontend http://localhost:${FRONTEND_PORT}

Options:
  --install          Always run dependency installation before startup
  --skip-install     Skip dependency installation checks
  --no-migrate       Skip Alembic migrations
  --no-open          Do not open the browser after startup
  --backend-port N   Backend port, default: ${BACKEND_PORT}
  --frontend-port N  Frontend port, default: ${FRONTEND_PORT}
  -h, --help         Show this help

Environment overrides:
  BACKEND_HOST, BACKEND_PORT, FRONTEND_HOST, FRONTEND_PORT
USAGE
}

log() {
  printf '[start] %s\n' "$*"
}

die() {
  printf '[start] ERROR: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

cleanup() {
  local status=$?
  trap - INT TERM EXIT
  if [ "${#PIDS[@]}" -gt 0 ]; then
    log "Stopping child processes..."
    kill "${PIDS[@]}" >/dev/null 2>&1 || true
    wait "${PIDS[@]}" >/dev/null 2>&1 || true
  fi
  exit "$status"
}

wait_for_url() {
  local name="$1"
  local url="$2"
  local pid="$3"
  local attempts="${4:-60}"
  local i=1

  while [ "$i" -le "$attempts" ]; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "$name is ready: $url"
      return 0
    fi
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      wait "$pid" || true
      die "$name process exited before becoming ready"
    fi
    sleep 1
    i=$((i + 1))
  done

  die "$name did not become ready in ${attempts}s: $url"
}

open_url() {
  local url="$1"
  if command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  else
    log "Browser auto-open skipped; open manually: $url"
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install)
      RUN_INSTALL="always"
      ;;
    --skip-install)
      RUN_INSTALL="never"
      ;;
    --no-migrate)
      RUN_MIGRATIONS=0
      ;;
    --no-open)
      OPEN_BROWSER=0
      ;;
    --backend-port)
      [ "$#" -ge 2 ] || die "--backend-port requires a value"
      BACKEND_PORT="$2"
      shift
      ;;
    --frontend-port)
      [ "$#" -ge 2 ] || die "--frontend-port requires a value"
      FRONTEND_PORT="$2"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
  shift
done

trap cleanup INT TERM EXIT

cd "$ROOT_DIR"

need_cmd uv
need_cmd pnpm
need_cmd curl

if [ ! -f "$BACKEND_DIR/.env" ]; then
  log "Creating backend/.env from backend/.env.example"
  cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
fi

if [ ! -f "$FRONTEND_DIR/.env.development" ] && [ -f "$FRONTEND_DIR/.env.example" ]; then
  log "Creating frontend/.env.development from frontend/.env.example"
  cp "$FRONTEND_DIR/.env.example" "$FRONTEND_DIR/.env.development"
fi

if [ "$RUN_INSTALL" = "always" ] || { [ "$RUN_INSTALL" = "auto" ] && [ ! -d "$BACKEND_DIR/.venv" ]; }; then
  log "Installing backend dependencies with uv"
  (cd "$BACKEND_DIR" && uv sync)
fi

if [ "$RUN_INSTALL" = "always" ] || { [ "$RUN_INSTALL" = "auto" ] && [ ! -d "$FRONTEND_DIR/node_modules" ]; }; then
  log "Installing frontend dependencies with pnpm"
  (cd "$FRONTEND_DIR" && pnpm install)
fi

if [ "$RUN_MIGRATIONS" -eq 1 ]; then
  log "Applying backend migrations"
  (cd "$BACKEND_DIR" && uv run alembic upgrade head)
fi

log "Starting backend on http://localhost:${BACKEND_PORT}"
(
  cd "$BACKEND_DIR"
  exec uv run uvicorn app.main:app \
    --reload \
    --workers 1 \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT"
) &
PIDS+=("$!")
BACKEND_PID="${PIDS[0]}"

wait_for_url "Backend" "http://127.0.0.1:${BACKEND_PORT}/api/v1/health" "$BACKEND_PID" 60

log "Starting frontend on http://localhost:${FRONTEND_PORT}"
(
  cd "$FRONTEND_DIR"
  exec pnpm exec vite \
    --host "$FRONTEND_HOST" \
    --port "$FRONTEND_PORT" \
    --strictPort
) &
PIDS+=("$!")
FRONTEND_PID="${PIDS[1]}"

wait_for_url "Frontend" "http://127.0.0.1:${FRONTEND_PORT}" "$FRONTEND_PID" 60

log "Ready."
log "Frontend: http://localhost:${FRONTEND_PORT}"
log "Backend:  http://localhost:${BACKEND_PORT}"
log "API docs: http://localhost:${BACKEND_PORT}/docs"

if [ "$OPEN_BROWSER" -eq 1 ]; then
  open_url "http://localhost:${FRONTEND_PORT}"
fi

log "Press Ctrl-C to stop."
while true; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      wait "$pid" || true
      die "A child process exited; stopping the stack"
    fi
  done
  sleep 2
done
