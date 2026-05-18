output "backend_url" {
  description = "Backend Cloud Run service URL"
  value       = google_cloud_run_v2_service.backend.uri
}

output "frontend_url" {
  description = "Frontend Cloud Run service URL"
  value       = google_cloud_run_v2_service.frontend.uri
}

output "langfuse_url" {
  description = "LangFuse Cloud Run service URL"
  value       = google_cloud_run_v2_service.langfuse.uri
}

output "litellm_url" {
  description = "LiteLLM Cloud Run service URL"
  value       = google_cloud_run_v2_service.litellm.uri
}

output "kong_url" {
  description = "Kong Cloud Run service URL"
  value       = google_cloud_run_v2_service.kong.uri
}

output "db_connection_name" {
  description = "Cloud SQL connection name for app DB"
  value       = google_sql_database_instance.finadvisor.connection_name
}

output "langfuse_db_connection_name" {
  description = "Cloud SQL connection name for LangFuse DB"
  value       = google_sql_database_instance.langfuse.connection_name
}

output "artifact_registry_url" {
  description = "Artifact Registry repository URL"
  value       = local.registry
}

output "corpus_bucket" {
  description = "GCS bucket for corpus data"
  value       = google_storage_bucket.corpus.name
}

output "eval_results_bucket" {
  description = "GCS bucket for eval results"
  value       = google_storage_bucket.eval_results.name
}
