# One table (HANDOFF §6.1, D7). No server_side_encryption block: the default AWS-owned key is free and
# still encrypts; enabling the block without a key switches to the KMS-billed aws/dynamodb key.
resource "aws_dynamodb_table" "main" {
  #checkov:skip=CKV_AWS_28:PITR is billed per GB-month with no free tier (I7); every result is reproducible by replay
  #checkov:skip=CKV_AWS_119:A customer-managed KMS key costs $1/month (I7); the AWS-owned key encrypts at rest
  #checkov:skip=CKV2_AWS_16:Fixed 10/10 provisioned capacity is the free tier (D7); autoscaling could leave it
  name                        = var.name
  billing_mode                = "PROVISIONED"
  read_capacity               = var.table_rcu
  write_capacity              = var.table_wcu
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = false

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = false
  }
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name}-shards-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "shards" {
  name                       = "${var.name}-shards"
  visibility_timeout_seconds = var.visibility_timeout_s
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true
}

resource "aws_sqs_queue_redrive_policy" "shards" {
  queue_url = aws_sqs_queue.shards.id
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

# TLS in transit: deny every SQS call made without TLS. A Deny with Principal "*" and sqs:* grants nothing.
data "aws_iam_policy_document" "tls_only" {
  for_each = {
    shards = aws_sqs_queue.shards.arn
    dlq    = aws_sqs_queue.dlq.arn
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [each.value]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_sqs_queue_policy" "shards" {
  queue_url = aws_sqs_queue.shards.id
  policy    = data.aws_iam_policy_document.tls_only["shards"].json
}

resource "aws_sqs_queue_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  policy    = data.aws_iam_policy_document.tls_only["dlq"].json
}
