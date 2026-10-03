locals {
  services = [
    "aiplatform.googleapis.com",
    "run.googleapis.com",
    "firestore.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudtrace.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "networkservices.googleapis.com",
    "compute.googleapis.com",
    "bigquery.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(locals.services)

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}
