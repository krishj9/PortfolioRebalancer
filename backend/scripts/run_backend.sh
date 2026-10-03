#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
REMOTE_AGENTS_DIR="${ROOT_DIR}/remote-agents"
MCP_SERVERS_DIR="${ROOT_DIR}/mcp-servers"

MODE="${1:-local}"
PORT="${PORT:-8000}"
RESEARCH_AGENT_PORT="${RESEARCH_AGENT_PORT:-8101}"
SENTIMENT_MCP_PORT="${SENTIMENT_MCP_PORT:-8201}"

if [[ "${MODE}" != "local" && "${MODE}" != "aws" ]]; then
  echo "Usage: $0 [local|aws]" >&2
  exit 1
fi

# Cleanup function to kill all background processes on exit
cleanup() {
  echo "Shutting down services..."
  jobs -p | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT

export PERSISTENCE_MODE="${MODE}"
export DYNAMODB_MODE="${MODE}"
if [[ "${MODE}" == "local" ]]; then
  export DYNAMODB_ENDPOINT_URL="${DYNAMODB_ENDPOINT_URL:-http://localhost:55000}"
else
  unset DYNAMODB_ENDPOINT_URL || true
fi

# Start Research Agent A2A Server
echo "Starting Research Agent A2A Server on port ${RESEARCH_AGENT_PORT}..."
cd "${REMOTE_AGENTS_DIR}/research-agent"
if [[ ! -d "venv" ]]; then
  echo "Creating virtual environment for Research Agent..."
  python3 -m venv venv
fi
source venv/bin/activate
pip install -q -r requirements.txt 2>/dev/null || true
python main.py &
RESEARCH_AGENT_PID=$!
echo "Research Agent started (PID: ${RESEARCH_AGENT_PID})"
sleep 2

# Start Sentiment Agent MCP Server
echo "Starting Sentiment Agent MCP Server on port ${SENTIMENT_MCP_PORT}..."
cd "${MCP_SERVERS_DIR}/sentiment"
if [[ ! -d "venv" ]]; then
  echo "Creating virtual environment for Sentiment Agent..."
  python3 -m venv venv
fi
source venv/bin/activate
pip install -q -r requirements.txt 2>/dev/null || true
python server.py &
SENTIMENT_MCP_PID=$!
echo "Sentiment Agent MCP Server started (PID: ${SENTIMENT_MCP_PID})"
sleep 2

# Start Backend
echo "Starting Backend on port ${PORT}..."
cd "${BACKEND_DIR}"
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port "${PORT}" &
BACKEND_PID=$!
echo "Backend started (PID: ${BACKEND_PID})"

# Wait for all services
echo ""
echo "=========================================="
echo "All services started successfully!"
echo "=========================================="
echo "Backend:                http://localhost:${PORT}"
echo "Research Agent A2A:     http://localhost:${RESEARCH_AGENT_PORT}"
echo "Sentiment Agent MCP:    http://localhost:${SENTIMENT_MCP_PORT}"
echo ""
echo "Press Ctrl+C to stop all services"
echo "=========================================="
echo ""

# Wait for any process to exit
wait -n 2>/dev/null || true
