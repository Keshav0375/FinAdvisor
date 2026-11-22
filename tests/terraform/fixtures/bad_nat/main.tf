# Negative fixture: scripts/check_no_nat.sh must reject this directory (ADR-024: no Cloud NAT).
resource "google_compute_router_nat" "forbidden" {
  name                               = "forbidden-nat"
  router                             = "router"
  region                             = "us-central1"
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}
