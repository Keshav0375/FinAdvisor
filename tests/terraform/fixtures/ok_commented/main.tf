# Positive fixture: mentions of the forbidden types in comments must not fail the check.
# resource "google_compute_router_nat" "not_real" {}
#   resource "google_vpc_access_connector" "not_real" {}
resource "google_compute_network" "fine" {
  name = "fine"
}
