terraform {
  # 1.7 is the first release with mock_provider, which tests/edge.tftest.hcl needs.
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  # Partial backend: bucket and prefix come from -backend-config at init (README.md, own prefix
  # terraform/edge/state, apart from the MVP root). validate and test never touch it.
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}
