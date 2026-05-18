resource "google_sql_database_instance" "finadvisor" {
  name             = "finadvisor-db-${var.environment}"
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier              = var.db_tier
    availability_type = var.environment == "prod" ? "REGIONAL" : "ZONAL"

    database_flags {
      name  = "cloudsql.enable_pgvector"
      value = "on"
    }

    backup_configuration {
      enabled                        = var.environment == "prod"
      point_in_time_recovery_enabled = var.environment == "prod"
    }

    ip_configuration {
      ipv4_enabled = true
    }
  }

  deletion_protection = var.environment == "prod"
}

resource "google_sql_database" "finadvisor" {
  name     = "finadvisor"
  instance = google_sql_database_instance.finadvisor.name
}

resource "google_sql_user" "finadvisor" {
  name     = "finadvisor"
  instance = google_sql_database_instance.finadvisor.name
  password = random_password.db_password.result
}

resource "random_password" "db_password" {
  length  = 32
  special = false
}

resource "google_secret_manager_secret_version" "db_url" {
  secret = google_secret_manager_secret.db_url.id
  secret_data = join("", [
    "postgresql+asyncpg://",
    google_sql_user.finadvisor.name,
    ":",
    random_password.db_password.result,
    "@/finadvisor?host=/cloudsql/",
    google_sql_database_instance.finadvisor.connection_name,
  ])
}

resource "google_sql_database_instance" "langfuse" {
  name             = "finadvisor-langfuse-db-${var.environment}"
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier = var.langfuse_db_tier

    ip_configuration {
      ipv4_enabled = true
    }
  }

  deletion_protection = false
}

resource "google_sql_database" "langfuse" {
  name     = "langfuse"
  instance = google_sql_database_instance.langfuse.name
}

resource "google_sql_user" "langfuse" {
  name     = "langfuse"
  instance = google_sql_database_instance.langfuse.name
  password = random_password.langfuse_db_password.result
}

resource "random_password" "langfuse_db_password" {
  length  = 32
  special = false
}
