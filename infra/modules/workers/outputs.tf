output "function_name" {
  description = "Worker function name."
  value       = aws_lambda_function.worker.function_name
}

output "log_group_name" {
  description = "Worker log group."
  value       = aws_cloudwatch_log_group.worker.name
}
