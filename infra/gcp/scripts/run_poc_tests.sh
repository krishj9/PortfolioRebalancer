#!/usr/bin/env bash
# ==============================================================================
# PortfolioRebalancer-GCP - Minimum POC Test Set Run Script (Task P4-04)
#
# Runs the minimum validated test suite for the GCP migration:
# 1. Deterministic parity & agent runtime integration
# 2. GCP adapters (Gemini, Firestore, Memory Bank, BigQuery Analytics, Authz)
# 3. Governance, security, and observability (Model Armor, Tool Bypass, OTel Traces, Retries)
#
# Usage:
#   ./infra/gcp/scripts/run_poc_tests.sh          # Run standard offline/mock test suite
#   ./infra/gcp/scripts/run_poc_tests.sh --live   # Include live GCP cloud smoke tests
# ==============================================================================

set -euo pipefail

# ANSI Color Codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"

RUN_LIVE=false

# Parse flags
for arg in "$@"; do
  case $arg in
    --live)
      RUN_LIVE=true
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--live]"
      echo "  --live    Run live GCP smoke tests requiring active cloud credentials/resources"
      exit 0
      ;;
    *)
      ;;
  esac
done

# Locate python / pytest
if [[ -f "${REPO_ROOT}/.venv/bin/pytest" ]]; then
  PYTEST="${REPO_ROOT}/.venv/bin/pytest"
elif command -v pytest &>/dev/null; then
  PYTEST="pytest"
else
  PYTEST="python3 -m pytest"
fi

echo -e "${BOLD}${CYAN}=====================================================================${NC}"
echo -e "${BOLD}${CYAN}       Portfolio Rebalancer GCP - Minimum POC Test Suite            ${NC}"
echo -e "${BOLD}${CYAN}=====================================================================${NC}"
echo -e "Repository root : ${REPO_ROOT}"
echo -e "Pytest runner   : ${PYTEST}"
echo -e "Live Cloud Tests: ${RUN_LIVE}\n"

cd "${REPO_ROOT}"

START_TIME=$(date +%s)
FAILED_STAGES=0

# Define the minimum POC test files
CORE_PARITY_TESTS=(
  "backend/tests/test_agent_runtime_app.py"
  "backend/tests/test_graph_parity.py"
  "backend/tests/test_tool_client.py"
  "backend/tests/test_tools_router.py"
)

GCP_ADAPTER_TESTS=(
  "backend/tests/test_gemini_adapter.py"
  "backend/tests/test_firestore_store.py"
  "backend/tests/test_idempotency.py"
  "backend/tests/test_memory_bank_adapter.py"
  "backend/tests/test_memory_routes.py"
  "backend/tests/test_authz.py"
  "backend/tests/test_rebalance.py"
)

GCP_OBSERVABILITY_SECURITY_TESTS=(
  "backend/tests/gcp/test_trace_linkage.py"
  "backend/tests/gcp/test_retry.py"
  "backend/tests/gcp/test_prompt_injection.py"
  "backend/tests/gcp/test_tool_bypass.py"
  "backend/tests/gcp/test_sessions.py"
  "backend/tests/gcp/test_memory_scenario.py"
  "backend/tests/gcp/test_runtime_smoke.py"
  "backend/tests/gcp/test_analytics.py"
)

# ------------------------------------------------------------------------------
# Stage 1: Core Parity & Tool Execution
# ------------------------------------------------------------------------------
echo -e "${BOLD}${YELLOW}--> [Stage 1/3] Running Core Parity & Agent Runtime Tests...${NC}"
if ${PYTEST} "${CORE_PARITY_TESTS[@]}" -v -m "not gcp_live"; then
  echo -e "${GREEN}✓ Stage 1 Passed: Core parity and tool contracts intact.${NC}\n"
else
  echo -e "${RED}✗ Stage 1 Failed: Core parity tests encountered failures.${NC}\n"
  FAILED_STAGES=$((FAILED_STAGES + 1))
fi

# ------------------------------------------------------------------------------
# Stage 2: GCP Adapters & Migration Persistence
# ------------------------------------------------------------------------------
echo -e "${BOLD}${YELLOW}--> [Stage 2/3] Running GCP Adapters, Persistence & Idempotency Tests...${NC}"
if ${PYTEST} "${GCP_ADAPTER_TESTS[@]}" -v -m "not gcp_live"; then
  echo -e "${GREEN}✓ Stage 2 Passed: Gemini, Firestore, Memory Bank, and Authz adapters valid.${NC}\n"
else
  echo -e "${RED}✗ Stage 2 Failed: GCP adapter tests encountered failures.${NC}\n"
  FAILED_STAGES=$((FAILED_STAGES + 1))
fi

# ------------------------------------------------------------------------------
# Stage 3: GCP Observability, Governance & Security
# ------------------------------------------------------------------------------
echo -e "${BOLD}${YELLOW}--> [Stage 3/3] Running GCP Observability, Governance & Security Tests...${NC}"
EXTRA_ARGS=()
if [[ "${RUN_LIVE}" != "true" ]]; then
  EXTRA_ARGS=("-m" "not gcp_live")
fi

if ${PYTEST} "${GCP_OBSERVABILITY_SECURITY_TESTS[@]}" -v "${EXTRA_ARGS[@]}"; then
  echo -e "${GREEN}✓ Stage 3 Passed: Tracing, retries, security guardrails, and sessions validated.${NC}\n"
else
  echo -e "${RED}✗ Stage 3 Failed: GCP governance and observability tests encountered failures.${NC}\n"
  FAILED_STAGES=$((FAILED_STAGES + 1))
fi

# ------------------------------------------------------------------------------
# Summary Report
# ------------------------------------------------------------------------------
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo -e "${BOLD}${CYAN}=====================================================================${NC}"
echo -e "${BOLD}${CYAN}                     POC Test Suite Summary                          ${NC}"
echo -e "${BOLD}${CYAN}=====================================================================${NC}"
echo -e "Total Duration : ${ELAPSED} seconds"

if [[ ${FAILED_STAGES} -eq 0 ]]; then
  echo -e "Overall Status : ${GREEN}${BOLD}ALL TEST STAGES PASSED (0 failures)${NC}"
  echo -e "Validation     : Ready for deployment and review."
  exit 0
else
  echo -e "Overall Status : ${RED}${BOLD}${FAILED_STAGES} TEST STAGE(S) FAILED${NC}"
  exit 1
fi
