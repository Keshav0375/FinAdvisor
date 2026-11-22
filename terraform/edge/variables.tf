variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for the edge subnet"
  type        = string
  default     = "us-central1"
}

variable "network_name" {
  description = "Name of the edge VPC"
  type        = string
  default     = "finadvisor-edge"
}

variable "subnet_name" {
  description = "Name of the edge subnet used by Cloud Run Direct VPC egress"
  type        = string
  default     = "finadvisor-edge-run"
}

variable "subnet_cidr" {
  description = "Primary CIDR range of the edge subnet"
  type        = string
  default     = "10.10.0.0/24"

  validation {
    condition     = can(cidrhost(var.subnet_cidr, 0))
    error_message = "subnet_cidr must be a valid CIDR range."
  }
}

variable "run_app_zone_name" {
  description = "Name of the Cloud DNS private zone that maps run.app to private.googleapis.com"
  type        = string
  default     = "finadvisor-run-app"
}

variable "private_googleapis_zone_name" {
  description = "Name of the Cloud DNS private zone that holds the private.googleapis.com VIP records"
  type        = string
  default     = "finadvisor-private-googleapis"
}

variable "private_googleapis_ips" {
  description = "Private Google Access VIPs behind private.googleapis.com (199.36.153.8/30)"
  type        = list(string)
  default     = ["199.36.153.8", "199.36.153.9", "199.36.153.10", "199.36.153.11"]
}

variable "dns_ttl" {
  description = "TTL in seconds for the DNS records"
  type        = number
  default     = 300
}
