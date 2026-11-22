# Network for the gateway edge (ADR-024): Cloud Run reaches internal run.app URLs through the VPC
# over Private Google Access. No Cloud NAT and no Serverless VPC Access connector, enforced by
# scripts/check_no_nat.sh.

resource "google_compute_network" "edge" {
  name                    = var.network_name
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "edge" {
  name                     = var.subnet_name
  region                   = var.region
  network                  = google_compute_network.edge.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true
}

# *.run.app -> private.googleapis.com, so a service with ingress "internal" is reached from the VPC.
resource "google_dns_managed_zone" "run_app" {
  name        = var.run_app_zone_name
  dns_name    = "run.app."
  description = "Maps run.app to private.googleapis.com for Cloud Run internal ingress"
  visibility  = "private"

  private_visibility_config {
    networks {
      network_url = google_compute_network.edge.id
    }
  }
}

resource "google_dns_record_set" "run_app_wildcard" {
  managed_zone = google_dns_managed_zone.run_app.name
  name         = "*.run.app."
  type         = "CNAME"
  ttl          = var.dns_ttl
  rrdatas      = ["private.googleapis.com."]
}

# The VIP records need a zone that owns private.googleapis.com., apart from the run.app zone.
resource "google_dns_managed_zone" "private_googleapis" {
  name        = var.private_googleapis_zone_name
  dns_name    = "private.googleapis.com."
  description = "Private Google Access VIPs for the edge VPC"
  visibility  = "private"

  private_visibility_config {
    networks {
      network_url = google_compute_network.edge.id
    }
  }
}

resource "google_dns_record_set" "private_googleapis" {
  managed_zone = google_dns_managed_zone.private_googleapis.name
  name         = "private.googleapis.com."
  type         = "A"
  ttl          = var.dns_ttl
  rrdatas      = var.private_googleapis_ips
}
