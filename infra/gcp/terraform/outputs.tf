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

output "vpc_network_name" {
  description = "Name of the VPC network"
  value       = google_compute_network.vpc.name
}

output "vpc_subnet_name" {
  description = "Name of the subnetwork"
  value       = google_compute_subnetwork.subnet.name
}

output "psc_network_attachment_id" {
  description = "Resource ID of the PSC network attachment for Agent Runtime"
  value       = google_compute_network_attachment.psc_attachment.id
}

output "load_balancer_ip" {
  description = "Public IP address of the external HTTP(S) Load Balancer"
  value       = google_compute_global_address.lb_ip.address
}

output "cloud_armor_policy_id" {
  description = "ID of the Cloud Armor security policy"
  value       = google_compute_security_policy.edge_security_policy.id
}


