output "function_name" {
  description = "API function name."
  value       = aws_lambda_function.api.function_name
}

output "function_url" {
  description = "Function URL."
  value       = aws_lambda_function_url.api.function_url
}

output "log_group_name" {
  description = "API log group."
  value       = aws_cloudwatch_log_group.api.name
}

output "client_role_arn" {
  description = "Invoke-only client role."
  value       = aws_iam_role.client.arn
}

output "grader_user_name" {
  description = "Grader user, or null."
  value       = one(aws_iam_user.grader[*].name)
}

output "state_machine_arn" {
  description = "Watcher state machine ARN, or null when enable_step_functions is false."
  value       = one(aws_sfn_state_machine.watcher[*].arn)
}
