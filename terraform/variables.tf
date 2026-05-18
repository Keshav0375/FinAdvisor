variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "artifact_registry" {
  description = "Artifact Registry repository URL"
  type        = string
  default     = ""
}

variable "backend_image" {
  description = "Backend container image tag"
  type        = string
  default     = "latest"
}

variable "frontend_image" {
  description = "Frontend container image tag"
  type        = string
  default     = "latest"
}

variable "litellm_image" {
  description = "LiteLLM proxy container image tag"
  type        = string
  default     = "latest"
}

variable "kong_image" {
  description = "Kong gateway container image tag"
  type        = string
  default     = "latest"
}

variable "db_tier" {
  description = "Cloud SQL machine tier"
  type        = string
  default     = "db-f1-micro"
}

variable "langfuse_db_tier" {
  description = "Cloud SQL machine tier for LangFuse DB"
  type        = string
  default     = "db-f1-micro"
}

variable "backend_cpu" {
  description = "CPU limit for backend Cloud Run service"
  type        = string
  default     = "1"
}

variable "backend_memory" {
  description = "Memory limit for backend Cloud Run service"
  type        = string
  default     = "512Mi"
}

variable "frontend_cpu" {
  description = "CPU limit for frontend Cloud Run service"
  type        = string
  default     = "1"
}

variable "frontend_memory" {
  description = "Memory limit for frontend Cloud Run service"
  type        = string
  default     = "256Mi"
}
