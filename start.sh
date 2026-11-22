#!/bin/sh
set -e

echo "=== Starting Katha AI Services for Render ==="

# Set default ports
PORT="${PORT:-8080}"
NODE_PORT="${NODE_PORT:-3000}"
export PORT
export NODE_PORT

echo "[1/2] Launching Node.js Collections & Excel Engine on port ${NODE_PORT}..."
node server.js &
NODE_PID=$!

# Wait briefly for Node server to initialize
sleep 2

echo "[2/2] Launching Python FastAPI / Uvicorn Server on port ${PORT}..."
# Handle graceful shutdown of background Node process
trap "kill $NODE_PID 2>/dev/null || true" EXIT INT TERM

exec python3 -m uvicorn api:app --host 0.0.0.0 --port "${PORT}"

# Updated: 2026-11-22
