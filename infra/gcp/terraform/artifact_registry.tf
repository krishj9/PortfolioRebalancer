resource "google_artifact_registry_repository" "repo" {
  provider = google-beta

  location      = var.region
  repository_id = var.app_name
  description   = "Docker container repository for Portfolio Rebalancer services"
  format        = "DOCKER"

  depends_on = [google_project_service.apis]
}
