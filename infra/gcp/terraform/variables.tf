variable "project_id" {
  description = "Google Cloud Project ID"
  type        = string
  default     = "mybrightday-dev"
}

variable "region" {
  description = "Primary GCP region for resources"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "app_name" {
  description = "Application name prefix"
  type        = string
  default     = "portfolio-rebalancer"
}

variable "container_image" {
  description = "Container image for Cloud Run services (API and Tools)"
  type        = string
  default     = "us-central1-docker.pkg.dev/mybrightday-dev/portfolio-rebalancer/backend:latest"
}

variable "tools_url" {
  description = "URL of deterministic tools service (defaults to rebalancer-tools Cloud Run service)"
  type        = string
  default     = ""
}

variable "agent_runtime_resource_name" {
  description = "Vertex AI Agent Runtime resource name"
  type        = string
  default     = ""
}

variable "billing_account_id" {
  description = "GCP Billing Account ID for budget alerts (optional)"
  type        = string
  default     = ""
}

variable "monthly_budget_amount" {
  description = "Monthly budget limit in USD"
  type        = number
  default     = 50
}
