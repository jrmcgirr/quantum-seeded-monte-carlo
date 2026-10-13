output "table_name" {
  description = "Table name."
  value       = aws_dynamodb_table.main.name
}

output "table_arn" {
  description = "Table ARN."
  value       = aws_dynamodb_table.main.arn
}

output "queue_name" {
  description = "Main queue name."
  value       = aws_sqs_queue.shards.name
}

output "queue_arn" {
  description = "Main queue ARN."
  value       = aws_sqs_queue.shards.arn
}

output "queue_url" {
  description = "Main queue URL."
  value       = aws_sqs_queue.shards.id
}

output "dlq_name" {
  description = "DLQ name."
  value       = aws_sqs_queue.dlq.name
}

output "dlq_url" {
  description = "DLQ URL."
  value       = aws_sqs_queue.dlq.id
}
