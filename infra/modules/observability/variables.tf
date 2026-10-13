variable "name" {
  description = "Resource name prefix (qmc-<env>)."
  type        = string
}

variable "account_id" {
  description = "This account id (topic policy)."
  type        = string
}

variable "alert_email" {
  description = "Email subscribed to the alerts topic."
  type        = string
}

variable "metrics_namespace" {
  description = "EMF namespace of the custom metrics."
  type        = string
}

variable "queue_name" {
  description = "Main queue name."
  type        = string
}

variable "dlq_name" {
  description = "DLQ name."
  type        = string
}

variable "worker_function" {
  description = "Worker function name."
  type        = string
}

variable "api_function" {
  description = "API function name."
  type        = string
}
