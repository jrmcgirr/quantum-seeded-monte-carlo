variable "name" {
  description = "Resource name prefix (qmc-<env>)."
  type        = string
}

variable "env" {
  description = "Environment (QMC_ENV)."
  type        = string
}

variable "build_sha" {
  description = "Build git sha (QMC_BUILD_SHA)."
  type        = string
}

variable "zip_path" {
  description = "Path to build/worker.zip."
  type        = string
}

variable "memory_mb" {
  description = "Memory in MB."
  type        = number
}

variable "timeout_s" {
  description = "Timeout in seconds."
  type        = number
}

variable "reserved_concurrency" {
  description = "Reserved concurrency, or null for none."
  type        = number
  default     = null
}

variable "max_concurrency" {
  description = "Event source mapping maximum_concurrency."
  type        = number
}

variable "log_retention_days" {
  description = "Log group retention."
  type        = number
}

variable "table_arn" {
  description = "Table ARN."
  type        = string
}

variable "table_name" {
  description = "Table name (TABLE_NAME)."
  type        = string
}

variable "queue_arn" {
  description = "Main queue ARN."
  type        = string
}

variable "queue_url" {
  description = "Main queue URL (QUEUE_URL)."
  type        = string
}

variable "metrics_namespace" {
  description = "EMF namespace (METRICS_NAMESPACE)."
  type        = string
}

variable "max_receive_count" {
  description = "Same value as the redrive policy (MAX_RECEIVE_COUNT)."
  type        = number
}

variable "enable_fault_injection" {
  description = "FAULT_INJECTION_ENABLED."
  type        = bool
}
