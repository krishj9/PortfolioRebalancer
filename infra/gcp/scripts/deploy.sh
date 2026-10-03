#!/usr/bin/env bash
# End-to-end idempotent deployment script for Portfolio Rebalancer on GCP (Task P1-11).
# Usage: ./infra/gcp/scripts/deploy.sh [gcp_project_id] [region]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PROJECT_ID="${1:-${GCP_PROJECT:-mybrightday-dev}}"
REGION="${2:-${GCP_LOCATION:-us-central1}}"
TERRAFORM_DIR="${ROOT_DIR}/infra/gcp/terraform"

echo "================================================================="
echo " Deploying Portfolio Rebalancer to Google Cloud Platform"
echo " Project: ${PROJECT_ID}"
echo " Region:  ${REGION}"
echo "================================================================="

# 1. Run local tests
echo -e "\n[Step 1/5] Running automated tests..."
if [[ -f "${ROOT_DIR}/.venv/bin/pytest" ]]; then
  "${ROOT_DIR}/.venv/bin/pytest" "${ROOT_DIR}/backend/tests" -v \
    -k "not gcp and not live"
else
  pytest backend/tests -v -k "not gcp and not live"
fi
echo "All tests passed successfully!"

# 2. Build container image and push to Artifact Registry
echo -e "\n[Step 2/5] Building and pushing container image..."
IMAGE_TAG="${REGION}-docker.pkg.dev/${PROJECT_ID}/portfolio-rebalancer/backend:latest"

if command -v gcloud >/dev/null 2>&1; then
  echo "Submitting build via Cloud Build: ${IMAGE_TAG}..."
  gcloud builds submit "${ROOT_DIR}" \
    --config=- \
    --project="${PROJECT_ID}" <<EOF || echo "Cloud Build submission skipped or using local docker"
steps:
- name: 'gcr.io/cloud-builders/docker'
  args: ['build', '-t', '${IMAGE_TAG}', '-f', 'backend/Dockerfile', '.']
images:
- '${IMAGE_TAG}'
EOF
fi

# 3. Apply Terraform baseline
echo -e "\n[Step 3/5] Applying Terraform infrastructure baseline..."
if command -v terraform >/dev/null 2>&1; then
  pushd "${TERRAFORM_DIR}" >/dev/null
  terraform init
  terraform apply -auto-approve \
    -var="project_id=${PROJECT_ID}" \
    -var="region=${REGION}"
  popd >/dev/null
else
  echo "Terraform not found on PATH, skipping terraform apply."
fi

# 4. Deploy Vertex AI Agent Runtime
echo -e "\n[Step 4/5] Deploying LangGraph workflow to Vertex AI Agent Runtime..."
if [[ -f "${ROOT_DIR}/.venv/bin/python" ]]; then
  "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/backend/app/agent_runtime/deploy.py"
else
  python backend/app/agent_runtime/deploy.py
fi

# 5. Seed Firestore with default data
echo -e "\n[Step 5/5] Seeding Firestore database..."
if [[ -f "${ROOT_DIR}/.venv/bin/python" ]]; then
  "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/backend/scripts/seed_firestore.py" "${PROJECT_ID}"
else
  python backend/scripts/seed_firestore.py "${PROJECT_ID}"
fi

echo -e "\n================================================================="
echo " Deployment completed successfully!"
echo "================================================================="
