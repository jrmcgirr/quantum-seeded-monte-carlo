# Unencrypted on purpose (D17): Budgets cannot publish to a topic encrypted with the AWS-managed SNS key,
# and a customer-managed key costs $1/month.
resource "aws_sns_topic" "alerts" {
  name = "${var.name}-alerts"
}

data "aws_iam_policy_document" "alerts" {
  statement {
    sid       = "AccountPublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.account_id}:root"]
    }
  }

  statement {
    sid       = "ServicePublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com", "budgets.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts.json
}

# Every apply from empty creates a new, unconfirmed subscription: confirm the email at once (R12).
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# HANDOFF §9.2: seven standard-resolution alarms (free tier: 10).
locals {
  alarms = {
    dlq-depth = {
      namespace  = "AWS/SQS", metric = "ApproximateNumberOfMessagesVisible", stat = "Maximum"
      dimensions = { QueueName = var.dlq_name }, period = 60, threshold = 0
      describe   = "A shard message reached the DLQ: a shard failed on every attempt."
    }
    worker-errors = {
      namespace  = "AWS/Lambda", metric = "Errors", stat = "Sum"
      dimensions = { FunctionName = var.worker_function }, period = 60, threshold = 0
      describe   = "The worker Lambda raised (including the fail-closed SIMD self-check)."
    }
    api-errors = {
      namespace  = "AWS/Lambda", metric = "Errors", stat = "Sum"
      dimensions = { FunctionName = var.api_function }, period = 60, threshold = 0
      describe   = "The API Lambda raised an unhandled error."
    }
    worker-throttles = {
      namespace  = "AWS/Lambda", metric = "Throttles", stat = "Sum"
      dimensions = { FunctionName = var.worker_function }, period = 60, threshold = 0
      describe   = "Worker invocations were throttled."
    }
    seeding-failures = {
      namespace  = var.metrics_namespace, metric = "SeedingFailures", stat = "Sum"
      dimensions = {}, period = 300, threshold = 0
      describe   = "A job was rejected because Qrypt or the token secret failed."
    }
    jobs-partial = {
      namespace  = var.metrics_namespace, metric = "JobsPartial", stat = "Sum"
      dimensions = {}, period = 60, threshold = 0
      describe   = "A job finished PARTIAL."
    }
    queue-age = {
      namespace  = "AWS/SQS", metric = "ApproximateAgeOfOldestMessage", stat = "Maximum"
      dimensions = { QueueName = var.queue_name }, period = 60, threshold = 120
      describe   = "The oldest shard message has waited more than 120 s."
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each = local.alarms

  alarm_name          = "${var.name}-${each.key}"
  alarm_description   = each.value.describe
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.stat
  dimensions          = each.value.dimensions
  period              = each.value.period
  evaluation_periods  = 1
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}
