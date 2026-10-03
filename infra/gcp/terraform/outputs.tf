output "api_service_url" {
  description = "URL of the rebalancer-api Cloud Run service"
  value       = google_cloud_run_v2_service.api.uri
}

output "tools_service_url" {
  description = "URL of the rebalancer-tools Cloud Run service"
  value       = google_cloud_run_v2_service.tools.uri
}

output "artifact_registry_repository" {
  description = "Artifact Registry Docker repository"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.app_name}"
}

output "firestore_database_name" {
  description = "Firestore database name"
  value       = google_firestore_database.database.name
}

output "sa_api_email" {
  description = "Email of the API service account"
  value       = google_service_account.sa_api.email
}

output "sa_tools_email" {
  description = "Email of the deterministic tools service account"
  value       = google_service_account.sa_tools.email
}

output "sa_runtime_email" {
  description = "Email of the Agent Runtime service account"
  value       = google_service_account.sa_runtime.email
}

output "sa_deployer_email" {
  description = "Email of the deployer service account"
  value       = google_service_account.sa_deployer.email
}

output "bigquery_dataset_id" {
  description = "BigQuery dataset ID for analytics"
  value       = google_bigquery_dataset.portfolio_analytics.dataset_id
}

output "bigquery_proposal_events_table_id" {
  description = "BigQuery table ID for proposal events"
  value       = google_bigquery_table.proposal_events.table_id
}
