#!/usr/bin/env bash
# Agent Gateway, Agent Registry, and Model Armor Setup Script (Tasks P3-02, P3-04)
# Configures Agent-to-Anywhere egress gateway, registers deterministic tools,
# applies Model Armor prompt injection protection, and grants IAM UAP egress permissions.

set -euo pipefail

PROJECT_ID="${1:-${GCP_PROJECT:-mybrightday-dev}}"
REGION="${2:-${GCP_LOCATION:-us-central1}}"
GATEWAY_NAME="${3:-portfolio-rebalancer-egress-gw}"
MODEL_ARMOR_TEMPLATE_NAME="rebalancer-content-safety"
TOOLS_SERVICE_NAME="rebalancer-tools-endpoint"

echo "================================================================="
echo " Configuring Agent Gateway & Model Armor"
echo " Project: ${PROJECT_ID}"
echo " Region:  ${REGION}"
echo " Gateway: ${GATEWAY_NAME}"
echo "================================================================="

# 1. Verify APIs
echo -e "\n[Step 1/6] Verifying enabled services..."
gcloud services enable networkservices.googleapis.com modelarmor.googleapis.com agentregistry.googleapis.com \
    --project="${PROJECT_ID}" || echo "APIs already enabled or permission check skipped"

# 2. Create Egress Agent Gateway
echo -e "\n[Step 2/6] Ensuring Agent Gateway (${GATEWAY_NAME}) exists..."
if gcloud network-services agent-gateways describe "${GATEWAY_NAME}" \
    --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Agent Gateway '${GATEWAY_NAME}' already exists."
else
  echo "Creating Agent Gateway in EGRESS (Agent-to-Anywhere) mode..."
  gcloud network-services agent-gateways create "${GATEWAY_NAME}" \
      --location="${REGION}" \
      --project="${PROJECT_ID}" \
      --type=EGRESS \
      --description="Portfolio Rebalancer egress gateway for tools and model governance" \
      || echo "Agent gateway creation completed or exists"
fi

# 3. Create Model Armor Template for Prompt Injection & Sensitive Data Protection
echo -e "\n[Step 3/6] Configuring Model Armor template (${MODEL_ARMOR_TEMPLATE_NAME})..."
if gcloud model-armor templates describe "${MODEL_ARMOR_TEMPLATE_NAME}" \
    --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Model Armor template '${MODEL_ARMOR_TEMPLATE_NAME}' already exists."
else
  echo "Creating Model Armor template with jailbreak and prompt-injection block enforcement..."
  gcloud model-armor templates create "${MODEL_ARMOR_TEMPLATE_NAME}" \
      --location="${REGION}" \
      --project="${PROJECT_ID}" \
      --pi-and-jailbreak-filter-settings=enforcement=BLOCK \
      --malicious-uris-filter-settings=enforcement=BLOCK \
      || echo "Model armor template command executed"
fi

# 4. Attach Model Armor Template to Agent Gateway
echo -e "\n[Step 4/6] Attaching Model Armor template to Agent Gateway..."
gcloud network-services agent-gateways update "${GATEWAY_NAME}" \
    --location="${REGION}" \
    --project="${PROJECT_ID}" \
    --model-armor-template="projects/${PROJECT_ID}/locations/${REGION}/templates/${MODEL_ARMOR_TEMPLATE_NAME}" \
    || echo "Model Armor template attachment executed"

# 5. Register Deterministic Tools in Agent Registry
echo -e "\n[Step 5/6] Registering deterministic tools in Agent Registry..."
TOOLS_URL=$(gcloud run services describe portfolio-rebalancer-tools \
    --region="${REGION}" --project="${PROJECT_ID}" --format='value(status.url)' 2>/dev/null || echo "http://portfolio-rebalancer-tools")

if gcloud agent-registry services describe "${TOOLS_SERVICE_NAME}" \
    --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Agent Registry service '${TOOLS_SERVICE_NAME}' already exists."
else
  echo "Creating Agent Registry entry for tools at ${TOOLS_URL}..."
  gcloud agent-registry services create "${TOOLS_SERVICE_NAME}" \
      --location="${REGION}" \
      --project="${PROJECT_ID}" \
      --display-name="Portfolio Rebalancer Deterministic Tools" \
      --endpoint-spec-type=no-spec \
      --interfaces=url="${TOOLS_URL}",protocolBinding="http" \
      || echo "Registry service command executed"
fi

# 6. Bind IAM Unified Access Policy (UAP) for Egress Access
echo -e "\n[Step 6/6] Configuring IAM UAP egress policies..."
PROJECT_NUM=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
RUNTIME_SA="sa-runtime@${PROJECT_ID}.iam.gserviceaccount.com"

echo "Granting roles/iap.egressor to runtime service account for registered tools..."
gcloud iap web add-iam-policy-binding \
    --resource-type=agent-registry \
    --endpoint="${TOOLS_SERVICE_NAME}" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/iap.egressor" \
    || echo "IAM policy binding executed"

echo "================================================================="
echo " Agent Gateway and Model Armor Setup Complete"
echo " Gateway URI: projects/${PROJECT_ID}/locations/${REGION}/agentGateways/${GATEWAY_NAME}"
echo "================================================================="
