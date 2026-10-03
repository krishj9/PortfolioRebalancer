# VPC, Subnet, and Network Attachment for Private Service Connect Interface (Architecture §4, Task P3-01)

data "google_project" "current" {
  project_id = var.project_id
}

# 1. Custom VPC Network
resource "google_compute_network" "vpc" {
  name                    = "${var.app_name}-vpc"
  auto_create_subnetworks = false
  description             = "VPC network for Portfolio Rebalancer private communication"
}

# 2. Subnet with Private Google Access enabled
# Minimum /28 required by Agent Runtime; /24 provides room for private services
resource "google_compute_subnetwork" "subnet" {
  name                     = "${var.app_name}-subnet"
  ip_cidr_range            = "10.0.0.0/24"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  private_ip_google_access = true
  description              = "Subnet with Private Google Access for PSC network attachment"
}

# 3. Network Attachment for Agent Runtime PSC Interface
resource "google_compute_network_attachment" "psc_attachment" {
  name                  = "${var.app_name}-psc-attachment"
  region                = var.region
  description           = "Network attachment for Vertex AI Agent Runtime Private Service Connect interface"
  connection_preference = "ACCEPT_AUTOMATIC"
  subnetworks           = [google_compute_subnetwork.subnet.id]
}

# 4. IAM Permissions for Vertex AI Service Agents to manage Network Attachment and traffic
resource "google_project_iam_member" "aiplatform_re_network_admin" {
  project = var.project_id
  role    = "roles/compute.networkAdmin"
  member  = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
}

resource "google_project_iam_member" "aiplatform_network_admin" {
  project = var.project_id
  role    = "roles/compute.networkAdmin"
  member  = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"
}

# 5. Cloud Router and Cloud NAT for secure outbound VPC traffic
resource "google_compute_router" "router" {
  name    = "${var.app_name}-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  name                               = "${var.app_name}-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}
