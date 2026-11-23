output "network" {
  description = "Name of the edge VPC, for Cloud Run Direct VPC egress (vpc_access network_interfaces)"
  value       = google_compute_network.edge.name
}

output "subnetwork" {
  description = "Name of the edge subnet, for Cloud Run Direct VPC egress (vpc_access network_interfaces)"
  value       = google_compute_subnetwork.edge.name
}

output "run_app_zone" {
  description = "Name of the run.app private DNS zone"
  value       = google_dns_managed_zone.run_app.name
}
