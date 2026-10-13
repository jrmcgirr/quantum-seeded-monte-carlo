locals {
  function_name = "${var.name}-worker"
  # HANDOFF §12, QMC-099. Provisional until WS0 pins the names for the exact NumPy version.
  npy_disable_cpu_features = "X86_V4 AVX512_ICL AVX512_SPR"
}

data "aws_caller_identity" "current" {}

# Created before the function, with retention, so Lambda never creates a never-expiring group that survives destroy.
resource "aws_cloudwatch_log_group" "worker" {
  #checkov:skip=CKV_AWS_158:A customer-managed KMS key costs $1/month (I7); CloudWatch Logs encrypts at rest by default
  #checkov:skip=CKV_AWS_338:14-day retention by design (I7, HANDOFF 9.3); results live in DynamoDB, not logs
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "worker" {
  name               = local.function_name
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

# HANDOFF §8.3. The only "*" is the log-stream suffix on this function's own log group.
data "aws_iam_policy_document" "worker" {
  statement {
    sid = "ShardQueue"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ChangeMessageVisibility",
    ]
    resources = [var.queue_arn]
  }

  statement {
    sid       = "Table"
    actions   = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [var.table_arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.worker.arn}:*"]
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = local.function_name
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

resource "aws_lambda_function" "worker" {
  #checkov:skip=CKV_AWS_50:X-Ray write actions need Resource "*" (D18)
  #checkov:skip=CKV_AWS_117:No private resources to reach; a VPC needs NAT or endpoints, which are not free
  #checkov:skip=CKV_AWS_173:No secrets in the environment (the Qrypt token stays in SSM); default AWS-managed encryption; a CMK costs $1/month
  #checkov:skip=CKV_AWS_272:Code signing needs S3 and a signing profile; zips are deterministic and hashed in build/manifest.json
  #checkov:skip=CKV_AWS_116:Invoked by the SQS event source mapping; failed messages go to the queue redrive DLQ, which is alarmed
  function_name                  = local.function_name
  role                           = aws_iam_role.worker.arn
  runtime                        = "python3.13"
  architectures                  = ["x86_64"]
  handler                        = "qmc_worker.handler.handle"
  memory_size                    = var.memory_mb
  timeout                        = var.timeout_s
  filename                       = var.zip_path
  source_code_hash               = filebase64sha256(var.zip_path)
  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = {
      QMC_ENV                     = var.env
      QMC_BUILD_SHA               = var.build_sha
      QMC_ENFORCE_PINNED_DISPATCH = "true"
      TABLE_NAME                  = var.table_name
      QUEUE_URL                   = var.queue_url
      METRICS_NAMESPACE           = var.metrics_namespace
      MAX_RECEIVE_COUNT           = tostring(var.max_receive_count)
      RETRY_VISIBILITY_S          = "5"
      NPY_DISABLE_CPU_FEATURES    = local.npy_disable_cpu_features
      FAULT_INJECTION_ENABLED     = tostring(var.enable_fault_injection)
      LOG_LEVEL                   = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.worker, aws_iam_role_policy.worker]
}

# Keep glibc and the runtime fixed under a deployed function (I5, §7.9). Provider 6.x has no
# runtime_management_config block on aws_lambda_function; this resource sets the same thing.
resource "aws_lambda_runtime_management_config" "worker" {
  function_name     = aws_lambda_function.worker.function_name
  update_runtime_on = "FunctionUpdate"
}

# The only event source mapping in the stack (I7). Never map the DLQ: it must stay full for its alarm.
resource "aws_lambda_event_source_mapping" "shards" {
  event_source_arn        = var.queue_arn
  function_name           = aws_lambda_function.worker.arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = var.max_concurrency
  }

  # IAM must have propagated before Lambda validates queue access.
  depends_on = [aws_iam_role_policy.worker]
}
