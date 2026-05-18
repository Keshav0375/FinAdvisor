resource "google_cloud_run_v2_service" "backend" {
  name     = "finadvisor-backend-${var.environment}"
  location = var.region

  template {
    service_account = google_service_account.backend.email

    scaling {
      min_instance_count = var.environment == "prod" ? 1 : 0
      max_instance_count = 10
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.finadvisor.connection_name]
      }
    }

    containers {
      image = "${local.registry}/backend:${var.backend_image}"

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = var.backend_cpu
          memory = var.backend_memory
        }
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_url.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "KONG_URL"
        value = "http://localhost:8000"
      }

      env {
        name = "ANTHROPIC_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.anthropic_api_key.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "VOYAGE_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.voyage_api_key.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "LANGFUSE_PUBLIC_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.langfuse_public_key.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "LANGFUSE_SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.langfuse_secret_key.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "LANGFUSE_HOST"
        value = google_cloud_run_v2_service.langfuse.uri
      }

      env {
        name  = "PII_MODE"
        value = "gcp_dlp"
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }

      env {
        name = "JWT_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.jwt_secret.secret_id
            version = "latest"
          }
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
    }
  }

  depends_on = [
    google_secret_manager_secret.db_url,
    google_secret_manager_secret.anthropic_api_key,
    google_secret_manager_secret.voyage_api_key,
  ]
}

resource "google_cloud_run_v2_service" "frontend" {
  name     = "finadvisor-frontend-${var.environment}"
  location = var.region

  template {
    service_account = google_service_account.frontend.email

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    containers {
      image = "${local.registry}/frontend:${var.frontend_image}"

      ports {
        container_port = 3000
      }

      resources {
        limits = {
          cpu    = var.frontend_cpu
          memory = var.frontend_memory
        }
      }

      env {
        name  = "NEXT_PUBLIC_API_URL"
        value = google_cloud_run_v2_service.backend.uri
      }
    }
  }
}

resource "google_cloud_run_v2_service" "litellm" {
  name     = "finadvisor-litellm-${var.environment}"
  location = var.region

  template {
    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    containers {
      image = "${local.registry}/litellm:${var.litellm_image}"

      ports {
        container_port = 4000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      env {
        name = "ANTHROPIC_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.anthropic_api_key.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "OPENAI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.openai_api_key.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "GOOGLE_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.google_api_key.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "LITELLM_MASTER_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.litellm_master_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }
}

resource "google_cloud_run_v2_service" "kong" {
  name     = "finadvisor-kong-${var.environment}"
  location = var.region

  template {
    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    containers {
      image = "${local.registry}/kong:${var.kong_image}"

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "256Mi"
        }
      }

      env {
        name  = "KONG_DATABASE"
        value = "off"
      }

      env {
        name  = "KONG_DECLARATIVE_CONFIG"
        value = "/etc/kong/kong.yml"
      }

      env {
        name  = "KONG_PROXY_LISTEN"
        value = "0.0.0.0:8000"
      }
    }
  }
}

resource "google_cloud_run_v2_service" "langfuse" {
  name     = "finadvisor-langfuse-${var.environment}"
  location = var.region

  template {
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    volumes {
      name = "cloudsql-langfuse"
      cloud_sql_instance {
        instances = [google_sql_database_instance.langfuse.connection_name]
      }
    }

    containers {
      image = "langfuse/langfuse:2"

      ports {
        container_port = 3000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      env {
        name = "DATABASE_URL"
        value = join("", [
          "postgresql://",
          google_sql_user.langfuse.name,
          ":",
          random_password.langfuse_db_password.result,
          "@/langfuse?host=/cloudsql/",
          google_sql_database_instance.langfuse.connection_name,
        ])
      }

      env {
        name  = "NEXTAUTH_SECRET"
        value = "changeme-in-production"
      }

      env {
        name  = "NEXTAUTH_URL"
        value = "https://langfuse.finadvisor.example.com"
      }

      volume_mounts {
        name       = "cloudsql-langfuse"
        mount_path = "/cloudsql"
      }
    }
  }
}
