# Plan-only assertions for terraform/edge/ with a mocked google provider: no credentials, no
# project. "demo-finadvisor" is a placeholder id that never reaches any API.

mock_provider "google" {}

variables {
  project_id = "demo-finadvisor"
}

run "subnet_has_private_google_access" {
  command = plan

  assert {
    condition     = google_compute_subnetwork.edge.private_ip_google_access == true
    error_message = "The edge subnet must have private_ip_google_access = true (ADR-024)."
  }

  assert {
    condition     = google_compute_network.edge.auto_create_subnetworks == false
    error_message = "The edge VPC must be custom mode (no auto-created subnets)."
  }

  assert {
    condition     = google_compute_subnetwork.edge.region == "us-central1"
    error_message = "The subnet region must come from var.region (default us-central1)."
  }
}

run "run_app_private_zone" {
  command = plan

  assert {
    condition     = google_dns_managed_zone.run_app.dns_name == "run.app."
    error_message = "The private zone must serve run.app. (trailing dot)."
  }

  assert {
    condition     = google_dns_managed_zone.run_app.visibility == "private"
    error_message = "The run.app zone must be private."
  }

  assert {
    condition     = length(google_dns_managed_zone.run_app.private_visibility_config) == 1
    error_message = "The run.app zone must be bound to exactly one VPC."
  }
}

run "run_app_records" {
  command = plan

  assert {
    condition     = google_dns_record_set.run_app_wildcard.name == "*.run.app."
    error_message = "The wildcard record must be *.run.app."
  }

  assert {
    condition     = google_dns_record_set.run_app_wildcard.type == "CNAME"
    error_message = "*.run.app. must be a CNAME."
  }

  assert {
    condition     = google_dns_record_set.run_app_wildcard.rrdatas == tolist(["private.googleapis.com."])
    error_message = "*.run.app. must point at private.googleapis.com."
  }

  assert {
    condition     = google_dns_record_set.private_googleapis.name == "private.googleapis.com."
    error_message = "The VIP record must be named private.googleapis.com."
  }

  assert {
    condition     = google_dns_record_set.private_googleapis.type == "A"
    error_message = "private.googleapis.com. must be an A record."
  }

  assert {
    condition     = toset(google_dns_record_set.private_googleapis.rrdatas) == toset(["199.36.153.8", "199.36.153.9", "199.36.153.10", "199.36.153.11"])
    error_message = "private.googleapis.com. must resolve to 199.36.153.8-11."
  }
}

run "private_googleapis_zone_is_separate" {
  command = plan

  assert {
    condition     = google_dns_managed_zone.private_googleapis.dns_name == "private.googleapis.com."
    error_message = "A VIP A record needs its own zone: it cannot live in the run.app zone."
  }

  assert {
    condition     = google_dns_managed_zone.private_googleapis.visibility == "private"
    error_message = "The private.googleapis.com zone must be private."
  }
}

run "values_come_from_variables" {
  command = plan

  variables {
    region       = "europe-west2"
    subnet_cidr  = "10.20.0.0/24"
    network_name = "demo-net"
  }

  assert {
    condition     = google_compute_subnetwork.edge.region == "europe-west2" && google_compute_subnetwork.edge.ip_cidr_range == "10.20.0.0/24"
    error_message = "Region and CIDR must follow the variables."
  }

  assert {
    condition     = google_compute_network.edge.name == "demo-net"
    error_message = "The network name must follow var.network_name."
  }
}

run "outputs_for_direct_vpc_egress" {
  command = plan

  assert {
    condition     = output.network == "finadvisor-edge" && output.subnetwork == "finadvisor-edge-run"
    error_message = "network and subnetwork must be exported for Cloud Run Direct VPC egress."
  }

  assert {
    condition     = output.run_app_zone == "finadvisor-run-app"
    error_message = "The run.app zone name must be exported."
  }
}
