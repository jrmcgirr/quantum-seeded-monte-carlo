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
  description = "Path to build/api.zip."
  type        = string
}

variable "memory_mb" {
  description = "Memory in MB."
  type        = number
}

variable "log_retention_days" {
  description = "Log group retention."
  type        = number
}

variable "account_id" {
  description = "This account's id (client role trust, Lambda confused-deputy condition)."
  type        = string
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

variable "qrypt_param_name" {
  description = "SSM parameter name of the Qrypt token (QRYPT_API_KEY_PARAM)."
  type        = string
}

variable "qrypt_param_arn" {
  description = "SSM parameter ARN, built as a string."
  type        = string
}

variable "qrypt_max_per_window" {
  description = "QRYPT_MAX_PER_WINDOW."
  type        = number
}

variable "qrypt_window_s" {
  description = "QRYPT_WINDOW_S (limiter window length, s)."
  type        = number
}

variable "max_paths" {
  description = "MAX_PATHS."
  type        = number
}

variable "max_shards" {
  description = "MAX_SHARDS."
  type        = number
}

variable "max_paths_per_shard" {
  description = "MAX_PATHS_PER_SHARD."
  type        = number
}

variable "job_deadline_s" {
  description = "JOB_DEADLINE_S."
  type        = number
}

variable "worker_memory_mb" {
  description = "WORKER_MEMORY_MB (cost estimate)."
  type        = number
}

variable "enable_fault_injection" {
  description = "FAULT_INJECTION_ENABLED."
  type        = bool
}

variable "create_grader_user" {
  description = "Create the grader user and its group."
  type        = bool
}
