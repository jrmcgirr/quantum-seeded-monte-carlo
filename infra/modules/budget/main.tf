# No time_period_start (never timestamp()): AWS defaults it to the current month and plans stay clean.
resource "aws_budgets_budget" "monthly" {
  name         = "qmc-monthly"
  budget_type  = "COST"
  limit_amount = var.limit_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [
      { type = "ACTUAL", threshold = 1 },
      { type = "ACTUAL", threshold = 100 },
      { type = "FORECASTED", threshold = 100 },
    ]

    content {
      comparison_operator        = "GREATER_THAN"
      notification_type          = notification.value.type
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      subscriber_sns_topic_arns  = [var.topic_arn]
      subscriber_email_addresses = [var.alert_email]
    }
  }
}
