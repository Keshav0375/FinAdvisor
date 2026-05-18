resource "google_secret_manager_secret" "db_url" {
  secret_id = "finadvisor-db-url"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "anthropic_api_key" {
  secret_id = "finadvisor-anthropic-api-key"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "openai_api_key" {
  secret_id = "finadvisor-openai-api-key"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "google_api_key" {
  secret_id = "finadvisor-google-api-key"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "voyage_api_key" {
  secret_id = "finadvisor-voyage-api-key"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "litellm_master_key" {
  secret_id = "finadvisor-litellm-master-key"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "jwt_secret" {
  secret_id = "finadvisor-jwt-secret"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "langfuse_public_key" {
  secret_id = "finadvisor-langfuse-public-key"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "langfuse_secret_key" {
  secret_id = "finadvisor-langfuse-secret-key"

  replication {
    auto {}
  }
}
