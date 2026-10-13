variable "env" {
  description = "Environment name; every resource is named qmc-<env>."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,15}$", var.env))
    error_message = "env must be lowercase letters, digits or hyphens, at most 16 characters."
  }
}

variable "region" {
  description = "AWS Region. The project is pinned to us-east-2 (contract change 001)."
  type        = string
  default     = "us-east-2"
}

variable "alert_email" {
  description = "Email that receives alarm and Budget notifications. Confirm the SNS subscription after every apply from empty."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be an email address."
  }
}

variable "qrypt_api_key_param_name" {
  description = "Name of the SecureString holding the Qrypt token, created out-of-band. Terraform only ever knows the name (I6)."
  type        = string
  default     = "/qmc/qrypt-api-key"

  validation {
    condition     = startswith(var.qrypt_api_key_param_name, "/")
    error_message = "qrypt_api_key_param_name must start with '/'."
  }
}

variable "table_rcu" {
  description = "Provisioned read capacity of the table (free tier: 25 per account and Region, shared)."
  type        = number
  default     = 10
}

variable "table_wcu" {
  description = "Provisioned write capacity of the table (free tier: 25 per account and Region, shared)."
  type        = number
  default     = 10
}

variable "max_receive_count" {
  description = "Receives before a shard message moves to the DLQ; also the worker's MAX_RECEIVE_COUNT."
  type        = number
  default     = 3

  validation {
    condition     = var.max_receive_count >= 1 && floor(var.max_receive_count) == var.max_receive_count
    error_message = "max_receive_count must be a positive integer."
  }
}

variable "worker_timeout_s" {
  description = "Worker Lambda timeout in seconds."
  type        = number
  default     = 10
}

variable "visibility_timeout_s" {
  description = "Main queue visibility timeout in seconds; at least 6 x worker_timeout_s."
  type        = number
  default     = 60

  validation {
    condition     = var.visibility_timeout_s >= 6 * var.worker_timeout_s
    error_message = "visibility_timeout_s must be at least 6 x worker_timeout_s."
  }
}

variable "worker_memory_mb" {
  description = "Worker Lambda memory in MB (also sent to the API as WORKER_MEMORY_MB for cost estimates)."
  type        = number
  default     = 1024
}

variable "api_memory_mb" {
  description = "API Lambda memory in MB."
  type        = number
  default     = 512
}

variable "worker_max_concurrency" {
  description = "Event source mapping maximum_concurrency: the fan-out cap (2-1000)."
  type        = number
  default     = 5

  validation {
    condition     = var.worker_max_concurrency >= 2 && var.worker_max_concurrency <= 1000
    error_message = "worker_max_concurrency must be between 2 and 1000."
  }
}

variable "worker_reserved_concurrency" {
  description = "Worker reserved concurrency. null (default) reserves nothing, so a low-quota account can apply; set it only if the account limit leaves 100 unreserved (see terraform.tfvars.example)."
  type        = number
  default     = null
}

variable "build_sha" {
  description = "Git sha of the build, from build/manifest.json (the Makefile passes it); deployed as QMC_BUILD_SHA."
  type        = string
  default     = "unknown"
}

variable "log_retention_days" {
  description = "Retention of every Lambda log group."
  type        = number
  default     = 14
}

variable "max_paths" {
  description = "Largest accepted paths per job (MAX_PATHS)."
  type        = number
  default     = 10000000
}

variable "max_shards" {
  description = "Largest accepted shards per job (MAX_SHARDS)."
  type        = number
  default     = 50
}

variable "max_paths_per_shard" {
  description = "Largest accepted ceil(paths / shards) (MAX_PATHS_PER_SHARD)."
  type        = number
  default     = 1000000
}

variable "qrypt_max_per_window" {
  description = "Qrypt calls allowed per fixed 10 s window (QRYPT_MAX_PER_WINDOW). 10 = 1 call/s on average (the Qrypt trial key's rate); any rolling 10 s <= 20, under the assignment's 30 (contract change 004)."
  type        = number
  default     = 10

  validation {
    condition     = var.qrypt_max_per_window >= 1 && var.qrypt_max_per_window <= 15
    error_message = "qrypt_max_per_window must be between 1 and 15."
  }
}

variable "job_deadline_s" {
  description = "Seconds after submit before the reaper may mark a job PARTIAL (JOB_DEADLINE_S)."
  type        = number
  default     = 900
}

variable "enable_fault_injection" {
  description = "Accept fault_injection.fail_shards in requests (demo only, D28)."
  type        = bool
  default     = false
}

variable "create_budget" {
  description = "Create the account-wide $1 monthly Budget. Only one environment per account should."
  type        = bool
  default     = true
}

variable "budget_limit_usd" {
  description = "Monthly Budget limit in USD, as a string."
  type        = string
  default     = "1.0"
}

variable "create_grader_user" {
  description = "Create the invoke-only IAM user qmc-<env>-grader. Its access key is created by CLI, never by Terraform (D26)."
  type        = bool
  default     = false
}

variable "api_zip" {
  description = "Path to the API zip built by scripts/build_lambdas.py."
  type        = string
  default     = "../build/api.zip"
}

variable "worker_zip" {
  description = "Path to the worker zip built by scripts/build_lambdas.py."
  type        = string
  default     = "../build/worker.zip"
}
