resource "google_artifact_registry_repository" "finadvisor" {
  location      = var.region
  repository_id = "finadvisor"
  format        = "DOCKER"
  description   = "Container images for FinAdvisor services"
}

locals {
  registry_url = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.finadvisor.repository_id}"
  registry     = var.artifact_registry != "" ? var.artifact_registry : local.registry_url
}
