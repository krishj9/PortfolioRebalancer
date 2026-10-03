# ── Cloud Run Services ────────────────────────────────────────────────────────

# 1. Deterministic Tools Service (Internal / IAM-authorized)
resource "google_cloud_run_v2_service" "tools" {
  name     = "${var.app_name}-tools"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.sa_tools.email

    containers {
      image = var.container_image

      env {
        name  = "APP_ROLE"
        value = "tools"
      }
      env {
        name  = "PERSISTENCE_MODE"
        value = "firestore"
      }
      env {
        name  = "FIRESTORE_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "FIRESTORE_DATABASE"
        value = google_firestore_database.database.name
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# IAM: Restrict tools service access to Runtime SA and API SA (no allUsers)
resource "google_cloud_run_v2_service_iam_member" "tools_invoker_runtime" {
  location = google_cloud_run_v2_service.tools.location
  name     = google_cloud_run_v2_service.tools.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.sa_runtime.email}"
}

resource "google_cloud_run_v2_service_iam_member" "tools_invoker_api" {
  location = google_cloud_run_v2_service.tools.location
  name     = google_cloud_run_v2_service.tools.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.sa_api.email}"
}

# 2. Main API Service
resource "google_cloud_run_v2_service" "api" {
  name     = "${var.app_name}-api"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.sa_api.email

    containers {
      image = var.container_image

      env {
        name  = "APP_ROLE"
        value = "api"
      }
      env {
        name  = "PERSISTENCE_MODE"
        value = "firestore"
      }
      env {
        name  = "FIRESTORE_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "FIRESTORE_DATABASE"
        value = google_firestore_database.database.name
      }
      env {
        name  = "ORCHESTRATION_MODE"
        value = "agent_runtime"
      }
      env {
        name  = "AGENT_RUNTIME_RESOURCE_NAME"
        value = var.agent_runtime_resource_name
      }
      env {
        name  = "TOOL_MODE"
        value = "remote"
      }
      env {
        name  = "TOOLS_URL"
        value = var.tools_url != "" ? var.tools_url : google_cloud_run_v2_service.tools.uri
      }
      env {
        name  = "LLM_PROVIDER"
        value = "gemini"
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# Public invoker for API service (Phase 1, protected in Phase 3 with IAP)
resource "google_cloud_run_v2_service_iam_member" "api_public_invoker" {
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
