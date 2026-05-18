resource "google_storage_bucket" "corpus" {
  name     = "${var.project_id}-finadvisor-corpus-${var.environment}"
  location = var.region

  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 3
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket" "eval_results" {
  name     = "${var.project_id}-finadvisor-eval-${var.environment}"
  location = var.region

  uniform_bucket_level_access = true
}
