resource "google_firestore_database" "database" {
  project     = var.project_id
  name        = "portfolio-rebalancer"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  delete_protection_state = "DELETE_PROTECTION_ENABLED"
  deletion_policy         = "ABANDON"

  depends_on = [google_project_service.apis]
}
