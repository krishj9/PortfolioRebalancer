#!/usr/bin/env bash
# Complete deployment script for AWS
# Usage: ./infra/scripts/deploy.sh [environment]
# Example: ./infra/scripts/deploy.sh dev

set -euo pipefail

ENVIRONMENT="${1:-dev}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_DIR="${ROOT_DIR}/infra/terraform/environments/${ENVIRONMENT}"

echo "🚀 Deploying Asset Management Platform to AWS (${ENVIRONMENT})"
echo "=================================================="

# Check prerequisites
echo ""
echo "📋 Checking prerequisites..."

if ! command -v aws >/dev/null 2>&1; then
  echo "❌ AWS CLI not found. Please install it first."
  exit 1
fi

if ! command -v terraform >/dev/null 2>&1; then
  echo "❌ Terraform not found. Please install it first."
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "❌ npm not found. Please install Node.js first."
  exit 1
fi

# Verify AWS credentials
if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "❌ AWS credentials not configured. Run 'aws configure' first."
  exit 1
fi

AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION=$(aws configure get region || echo "us-east-1")
echo "✅ AWS Account: ${AWS_ACCOUNT}"
echo "✅ AWS Region: ${AWS_REGION}"

# Check if tfvars exists
if [[ ! -f "${TF_DIR}/${ENVIRONMENT}.tfvars" ]]; then
  echo ""
  echo "⚠️  ${ENVIRONMENT}.tfvars not found. Creating from example..."
  cp "${TF_DIR}/${ENVIRONMENT}.tfvars.example" "${TF_DIR}/${ENVIRONMENT}.tfvars"
  echo "📝 Please edit ${TF_DIR}/${ENVIRONMENT}.tfvars with your settings"
  echo "   Then run this script again."
  exit 0
fi

# Step 1: Package Lambda functions
echo ""
echo "📦 Step 1/4: Packaging Lambda functions..."
"${ROOT_DIR}/infra/scripts/package_lambda.sh"
echo "✅ Lambda functions packaged"

# Step 2: Initialize Terraform
echo ""
echo "🔧 Step 2/4: Initializing Terraform..."
cd "${TF_DIR}"
terraform init -upgrade
echo "✅ Terraform initialized"

# Step 3: Deploy infrastructure
echo ""
echo "🏗️  Step 3/4: Deploying infrastructure..."
echo "   This will create:"
echo "   - 6 DynamoDB tables"
echo "   - 3 Lambda functions"
echo "   - 1 API Gateway"
echo "   - 1 S3 bucket"
echo "   - IAM roles and CloudWatch logs"
echo ""

terraform plan -var-file="${ENVIRONMENT}.tfvars" -out=tfplan

read -p "Do you want to apply this plan? (yes/no): " -r
if [[ ! $REPLY =~ ^[Yy]es$ ]]; then
  echo "❌ Deployment cancelled"
  exit 0
fi

terraform apply tfplan
rm -f tfplan
echo "✅ Infrastructure deployed"

# Get outputs
API_BASE_URL=$(terraform output -raw api_base_url)
FRONTEND_BUCKET=$(terraform output -raw frontend_bucket_name)
FRONTEND_URL=$(terraform output -raw frontend_website_url)

# Step 4: Deploy frontend
echo ""
echo "🎨 Step 4/4: Deploying frontend..."
cd "${ROOT_DIR}"
"${ROOT_DIR}/infra/scripts/publish_frontend.sh" "${API_BASE_URL}" "${FRONTEND_BUCKET}"
echo "✅ Frontend deployed"

# Summary
echo ""
echo "=================================================="
echo "🎉 Deployment Complete!"
echo "=================================================="
echo ""
echo "📊 Deployment Summary:"
echo "   Environment:    ${ENVIRONMENT}"
echo "   AWS Account:    ${AWS_ACCOUNT}"
echo "   AWS Region:     ${AWS_REGION}"
echo ""
echo "🔗 URLs:"
echo "   API Gateway:    ${API_BASE_URL}"
echo "   Frontend:       ${FRONTEND_URL}"
echo ""
echo "📋 Next Steps:"
echo "   1. Open frontend: ${FRONTEND_URL}"
echo "   2. Test API:      curl ${API_BASE_URL}/health"
echo "   3. View logs:     aws logs tail /aws/lambda/asset-management-${ENVIRONMENT}-backend --follow"
echo ""
echo "📚 Documentation:"
echo "   - Deployment Guide: AWS_DEPLOYMENT_GUIDE.md (see Command Reference)"
echo "   - Startup Guide:    STARTUP_GUIDE.md"
echo ""
echo "💰 Estimated Cost: ~\$2-5/month for development"
echo ""
echo "🧹 To cleanup: cd ${TF_DIR} && terraform destroy -var-file=${ENVIRONMENT}.tfvars"
echo ""
