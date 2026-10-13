output "alerts_topic_arn" {
  description = "Alerts topic ARN, taken from the topic policy so consumers wait for the publish permission."
  value       = aws_sns_topic_policy.alerts.arn
}
