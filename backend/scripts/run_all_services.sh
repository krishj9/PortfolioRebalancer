#!/usr/bin/env bash
# Run all services (backend, research agent, sentiment MCP) in separate processes
# This script is useful for development and testing

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
  echo ""
  echo "Shutting down all services..."
  jobs -p | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true
  echo "All services stopped."
}
trap cleanup EXIT INT TERM

export PERSISTENCE_MODE="${MODE}"
export DYNAMODB_MODE="${MODE}"
if [[ "${MODE}" == "local" ]]; then
  export DYNAMODB_ENDPOINT_URL="${DYNAMODB_ENDPOINT_URL:-http://localhost:55000}"
else
  unset DYNAMODB_ENDPOINT_URL || true
fi

# Function to setup and run a service
run_service() {
  local service_name=$1
  local service_dir=$2
  local command=$3
  local port=$4
  
  echo "Starting ${service_name}..."
  (
    cd "${service_dir}"
    
    # Setup virtual environment if needed
    if [[ ! -d "venv" ]]; then
      echo "[${service_name}] Creating virtual environment..."
      python3 -m venv venv
    fi
    
    # Activate venv and install dependencies
    source venv/bin/activate
    if [[ -f "requirements.txt" ]]; then
      pip install -q -r requirements.txt 2>/dev/null || true
    fi
    
    # Run the service
    echo "[${service_name}] Starting on port ${port}..."
    eval "${command}"
  ) &
  
  local pid=$!
  echo "[${service_name}] Started (PID: ${pid})"
  sleep 2
}

# Start all services
run_service "Research Agent A2A" "${REMOTE_AGENTS_DIR}/research-agent" "python main.py" "${RESEARCH_AGENT_PORT}"
run_service "Sentiment Agent MCP" "${MCP_SERVERS_DIR}/sentiment" "python server.py" "${SENTIMENT_MCP_PORT}"
run_service "Backend" "${BACKEND_DIR}" "uv run uvicorn app.main:app --reload --host 0.0.0.0 --port ${PORT}" "${PORT}"

# Display startup information
echo ""
echo "=========================================="
echo "All services started successfully!"
echo "=========================================="
echo "Backend:                http://localhost:${PORT}"
echo "Research Agent A2A:     http://localhost:${RESEARCH_AGENT_PORT}"
echo "Sentiment Agent MCP:    http://localhost:${SENTIMENT_MCP_PORT}"
echo ""
echo "Health Checks:"
echo "  Backend:              curl http://localhost:${PORT}/health"
echo "  Research Agent:       curl http://localhost:${RESEARCH_AGENT_PORT}/health"
echo "  Sentiment Agent:      curl http://localhost:${SENTIMENT_MCP_PORT}/health"
echo ""
echo "Press Ctrl+C to stop all services"
echo "=========================================="
echo ""

# Wait for all background processes
wait
