# Negative fixture: scripts/check_no_nat.sh must reject this directory (ADR-024: Direct VPC egress,
# no Serverless VPC Access connector).
resource "google_vpc_access_connector" "forbidden" {
  name          = "forbidden-connector"
  region        = "us-central1"
  network       = "default"
  ip_cidr_range = "10.8.0.0/28"
}
