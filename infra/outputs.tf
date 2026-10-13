output "api_url" {
  description = "Function URL of the API (AWS_IAM: sign requests with clients/qmc.py)."
  value       = module.api.function_url
}

output "table_name" {
  description = "DynamoDB table."
  value       = module.data.table_name
}

output "queue_url" {
  description = "Main shard queue."
  value       = module.data.queue_url
}

output "dlq_url" {
  description = "Dead-letter queue (alarmed, never consumed)."
  value       = module.data.dlq_url
}

output "worker_function" {
  description = "Worker Lambda name."
  value       = module.workers.function_name
}

output "api_function" {
  description = "API Lambda name."
  value       = module.api.function_name
}

output "client_role_arn" {
  description = "Invoke-only role for callers in this account."
  value       = module.api.client_role_arn
}

output "grader_user_name" {
  description = "Invoke-only grader user (null unless create_grader_user). Create its access key by CLI."
  value       = module.api.grader_user_name
}

output "alerts_topic_arn" {
  description = "SNS topic for alarms and the Budget."
  value       = module.observability.alerts_topic_arn
}

output "log_groups" {
  description = "Lambda log groups."
  value = {
    api    = module.api.log_group_name
    worker = module.workers.log_group_name
  }
}
