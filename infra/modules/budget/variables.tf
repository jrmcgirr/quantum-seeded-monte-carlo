variable "limit_usd" {
  description = "Monthly limit in USD, as a string."
  type        = string
}

variable "topic_arn" {
  description = "Alerts topic ARN (must already allow budgets.amazonaws.com to publish)."
  type        = string
}
