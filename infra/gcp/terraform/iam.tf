# Service Accounts for Architecture §4

# 1. API Service Account (runs Cloud Run API)
resource "google_service_account" "sa_api" {
  account_id   = "sa-api"
  display_name = "Portfolio Rebalancer API Service Account"
}

# 2. Tools Service Account (runs Cloud Run deterministic tools)
resource "google_service_account" "sa_tools" {
  account_id   = "sa-tools"
  display_name = "Portfolio Rebalancer Deterministic Tools Service Account"
}

# 3. Runtime Service Account (Agent Runtime identity)
resource "google_service_account" "sa_runtime" {
  account_id   = "sa-runtime"
  display_name = "Portfolio Rebalancer Agent Runtime Service Account"
}

# 4. Deployer Service Account (CI/CD deployments)
resource "google_service_account" "sa_deployer" {
  account_id   = "sa-deployer"
  display_name = "Portfolio Rebalancer Deployer Service Account"
}

# ── IAM Role Bindings ────────────────────────────────────────────────────────

# sa-api roles: Datastore user (Firestore access), Vertex AI user (query runtime), BigQuery user
resource "google_project_iam_member" "sa_api_datastore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.sa_api.email}"
}

resource "google_project_iam_member" "sa_api_aiplatform" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.sa_api.email}"
}

resource "google_project_iam_member" "sa_api_bigquery_editor" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.sa_api.email}"
}

resource "google_project_iam_member" "sa_api_bigquery_jobuser" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.sa_api.email}"
}

# sa-tools roles: Datastore user (Firestore access), BigQuery editor/jobUser
resource "google_project_iam_member" "sa_tools_datastore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.sa_tools.email}"
}

resource "google_project_iam_member" "sa_tools_bigquery_editor" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.sa_tools.email}"
}

resource "google_project_iam_member" "sa_tools_bigquery_jobuser" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.sa_tools.email}"
}

# sa-runtime roles: Vertex AI user
resource "google_project_iam_member" "sa_runtime_aiplatform" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.sa_runtime.email}"
}

# sa-deployer roles: Run Admin, Artifact Registry Writer, AI Platform User
resource "google_project_iam_member" "sa_deployer_run" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.sa_deployer.email}"
}

resource "google_project_iam_member" "sa_deployer_ar" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.sa_deployer.email}"
}

resource "google_project_iam_member" "sa_deployer_aiplatform" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.sa_deployer.email}"
}

# Allow deployer to act as the runtime and service SAs
resource "google_service_account_iam_member" "deployer_user_api" {
  service_account_id = google_service_account.sa_api.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.sa_deployer.email}"
}

resource "google_service_account_iam_member" "deployer_user_tools" {
  service_account_id = google_service_account.sa_tools.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.sa_deployer.email}"
}

resource "google_service_account_iam_member" "deployer_user_runtime" {
  service_account_id = google_service_account.sa_runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.sa_deployer.email}"
}
