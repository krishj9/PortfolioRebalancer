# ── Edge Ingress: External Load Balancer, Cloud Armor, and IAP (Task P3-05) ───

# 1. Cloud Armor Security Policy with OWASP Top 10 Mitigations and Rate Limiting
resource "google_compute_security_policy" "edge_security_policy" {
  name        = "${var.app_name}-edge-armor"
  description = "Cloud Armor security policy for Portfolio Rebalancer edge ingress (Task P3-05)"

  # SQL Injection preconfigured rule
  rule {
    action   = "deny(403)"
    priority = "1000"
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('sqli-v33-stable')"
      }
    }
    description = "Deny requests matching SQL injection patterns"
  }

  # Cross-Site Scripting (XSS) preconfigured rule
  rule {
    action   = "deny(403)"
    priority = "1001"
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('xss-v33-stable')"
      }
    }
    description = "Deny requests matching XSS patterns"
  }

  # Local File Inclusion (LFI) preconfigured rule
  rule {
    action   = "deny(403)"
    priority = "1002"
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('lfi-v33-stable')"
      }
    }
    description = "Deny requests matching LFI patterns"
  }

  # Rate limiting rule: 120 requests/minute per client IP
  rule {
    action   = "throttle"
    priority = "2000"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      rate_limit_threshold {
        count        = 120
        interval_sec = 60
      }
      enforce_on_key = "IP"
    }
    description = "Rate limit traffic to 120 req/min per IP"
  }

  # Default allow rule
  rule {
    action   = "allow"
    priority = "2147483647"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "Default allow rule for benign traffic"
  }
}

# 2. Serverless Network Endpoint Group (NEG) for Cloud Run API
resource "google_compute_region_network_endpoint_group" "api_neg" {
  name                  = "${var.app_name}-api-neg"
  network_endpoint_type = "SERVERLESS"
  region                = var.region

  cloud_run {
    service = google_cloud_run_v2_service.api.name
  }
}

# 3. Static Assets Storage Bucket for Frontend UI
resource "google_storage_bucket" "frontend" {
  name                        = "${var.project_id}-${var.app_name}-frontend"
  location                    = var.region
  uniform_bucket_level_access = true

  website {
    main_page_suffix = "index.html"
    not_found_page   = "index.html"
  }
}

resource "google_storage_bucket_iam_member" "frontend_public" {
  bucket = google_storage_bucket.frontend.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}

# 4. Backend Bucket for Frontend UI
resource "google_compute_backend_bucket" "frontend_backend" {
  name        = "${var.app_name}-frontend-backend"
  bucket_name = google_storage_bucket.frontend.name
  enable_cdn  = true
}

# 5. Backend Service for Cloud Run API (Attached to Cloud Armor & optional IAP)
resource "google_compute_backend_service" "api_backend" {
  name                  = "${var.app_name}-api-backend"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  security_policy       = google_compute_security_policy.edge_security_policy.id

  backend {
    group = google_compute_region_network_endpoint_group.api_neg.id
  }

  dynamic "iap" {
    for_each = var.iap_client_id != "" ? [1] : []
    content {
      oauth2_client_id     = var.iap_client_id
      oauth2_client_secret = var.iap_client_secret
    }
  }
}

# 6. Global URL Map Routing: /api/* -> Cloud Run API, Default -> Frontend UI Bucket
resource "google_compute_url_map" "url_map" {
  name            = "${var.app_name}-lb-url-map"
  default_service = google_compute_backend_bucket.frontend_backend.id

  host_rule {
    hosts        = ["*"]
    path_matcher = "allpaths"
  }

  path_matcher {
    name            = "allpaths"
    default_service = google_compute_backend_bucket.frontend_backend.id

    path_rule {
      paths = [
        "/api/*",
        "/healthz",
        "/stream/*",
        "/docs",
        "/openapi.json"
      ]
      service = google_compute_backend_service.api_backend.id
    }
  }
}

# 7. Global Reserved Public IP and Forwarding Rule
resource "google_compute_global_address" "lb_ip" {
  name = "${var.app_name}-lb-ip"
}

resource "google_compute_target_http_proxy" "http_proxy" {
  name    = "${var.app_name}-http-proxy"
  url_map = google_compute_url_map.url_map.id
}

resource "google_compute_global_forwarding_rule" "http_forwarding_rule" {
  name                  = "${var.app_name}-http-forwarding-rule"
  target                = google_compute_target_http_proxy.http_proxy.id
  port_range            = "80"
  ip_protocol           = "TCP"
  ip_address            = google_compute_global_address.lb_ip.address
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# 8. Service Account IAM Grant for IAP to Invoke Cloud Run API
resource "google_cloud_run_v2_service_iam_member" "api_iap_invoker" {
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-iap.iam.gserviceaccount.com"
}
