# Unencrypted on purpose (D17): Budgets cannot publish to a topic encrypted with the AWS-managed SNS key,
# and a customer-managed key costs $1/month.
resource "aws_sns_topic" "alerts" {
  #checkov:skip=CKV_AWS_26:Budgets cannot publish to a topic using the AWS-managed SNS key, and a CMK costs $1/month (D17)
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

# QMC-118: the visible evidence for the EMF custom metrics (1 of 3 free dashboards). Metric and alarm widgets only:
# Logs Insights widgets bill a query on every view.
data "aws_region" "current" {}

locals {
  ns     = var.metrics_namespace
  region = data.aws_region.current.region
  widget = { width = 8, height = 6 }
  dashboard_widgets = [
    { title = "Jobs (sum per 5 min)", metrics = [
      [local.ns, "JobsSubmitted", { stat = "Sum" }],
      [local.ns, "JobsCompleted", { stat = "Sum" }],
      [local.ns, "JobsPartial", { stat = "Sum" }],
    ] },
    { title = "Paths simulated (sum per 5 min)", metrics = [
      [local.ns, "PathsSimulated", { stat = "Sum" }],
    ] },
    { title = "Qrypt entropy and seeding failures", metrics = [
      [local.ns, "EntropyKiB", { stat = "Sum" }],
      [local.ns, "SeedingFailures", { stat = "Sum" }],
    ] },
    { title = "Qrypt latency (ms)", metrics = [
      [local.ns, "QryptLatencyMs", { stat = "p50" }],
      [local.ns, "QryptLatencyMs", { stat = "p95" }],
    ] },
    { title = "Worker duration (ms)", metrics = [
      ["AWS/Lambda", "Duration", "FunctionName", var.worker_function, { stat = "p50" }],
      ["AWS/Lambda", "Duration", "FunctionName", var.worker_function, { stat = "Maximum" }],
    ] },
    { title = "Worker errors, throttles, shard failures", metrics = [
      ["AWS/Lambda", "Errors", "FunctionName", var.worker_function, { stat = "Sum" }],
      ["AWS/Lambda", "Throttles", "FunctionName", var.worker_function, { stat = "Sum" }],
      [local.ns, "ShardFailures", { stat = "Sum" }],
    ] },
    { title = "Shard queue: depth and oldest age (s)", metrics = [
      ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.queue_name, { stat = "Maximum" }],
      ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", var.queue_name, { stat = "Maximum", yAxis = "right" }],
    ] },
    { title = "DLQ depth", metrics = [
      ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.dlq_name, { stat = "Maximum" }],
    ] },
  ]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = var.name
  dashboard_body = jsonencode({
    widgets = concat(
      [for i, w in local.dashboard_widgets : {
        type   = "metric"
        x      = (i % 3) * local.widget.width
        y      = floor(i / 3) * local.widget.height
        width  = local.widget.width
        height = local.widget.height
        properties = {
          title   = w.title
          region  = local.region
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = w.metrics
        }
      }],
      [{
        type   = "alarm"
        x      = 16
        y      = 12
        width  = 8
        height = 6
        properties = {
          title  = "Alarms"
          alarms = sort([for a in aws_cloudwatch_metric_alarm.this : a.arn])
        }
      }],
    )
  })
}
