locals {
  function_name = "${var.name}-api"
  # Built from the name, not aws_lambda_function.api.arn, which goes unknown during an in-place update and would
  # make every build_sha bump re-plan the invoke policies.
  function_arn = "arn:aws:lambda:${data.aws_region.current.region}:${var.account_id}:function:${local.function_name}"
}

data "aws_region" "current" {}

# Created before the function, with retention, so Lambda never creates a never-expiring group that survives destroy.
resource "aws_cloudwatch_log_group" "api" {
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
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = local.function_name
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

# HANDOFF §8.3. The only "*" is the log-stream suffix on this function's own log group.
# No kms:Decrypt: the aws/ssm key policy authorizes account principals through SSM. Verified at G1: GetParameter
# with WithDecryption succeeded with ssm:GetParameter alone (WS2, job 5358ec35af8a450d9eba939da9f610d9).
data "aws_iam_policy_document" "api" {
  statement {
    sid = "Table"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:BatchGetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
    ]
    resources = [var.table_arn]
  }

  statement {
    sid       = "ShardQueue"
    actions   = ["sqs:SendMessage"]
    resources = [var.queue_arn]
  }

  statement {
    sid       = "QryptToken"
    actions   = ["ssm:GetParameter"]
    resources = [var.qrypt_param_arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = local.function_name
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

# No reserved concurrency: 0 would disable the Function URL.
resource "aws_lambda_function" "api" {
  #checkov:skip=CKV_AWS_50:X-Ray write actions need Resource "*" (D18)
  #checkov:skip=CKV_AWS_117:No private resources to reach; a VPC needs NAT or endpoints, which are not free
  #checkov:skip=CKV_AWS_173:No secrets in the environment (the Qrypt token stays in SSM); default AWS-managed encryption; a CMK costs $1/month
  #checkov:skip=CKV_AWS_272:Code signing needs S3 and a signing profile; zips are deterministic and hashed in build/manifest.json
  #checkov:skip=CKV_AWS_115:Reserved concurrency 0 disables the Function URL; submits are capped by the Qrypt limiter instead
  #checkov:skip=CKV_AWS_116:Invoked synchronously by the Function URL; an async DLQ never applies
  function_name    = local.function_name
  role             = aws_iam_role.api.arn
  runtime          = "python3.13"
  architectures    = ["x86_64"]
  handler          = "qmc_api.handler.handle"
  memory_size      = var.memory_mb
  timeout          = 10
  filename         = var.zip_path
  source_code_hash = filebase64sha256(var.zip_path)

  environment {
    variables = {
      QMC_ENV                 = var.env
      QMC_BUILD_SHA           = var.build_sha
      TABLE_NAME              = var.table_name
      QUEUE_URL               = var.queue_url
      METRICS_NAMESPACE       = var.metrics_namespace
      QRYPT_API_KEY_PARAM     = var.qrypt_param_name
      QRYPT_URL               = "https://api-eus.qrypt.com/api/v1/quantum-entropy"
      QRYPT_DEADLINE_MS       = "2500"
      QRYPT_MAX_PER_WINDOW    = tostring(var.qrypt_max_per_window)
      QRYPT_WINDOW_S          = tostring(var.qrypt_window_s)
      SECRET_CACHE_TTL_S      = "300"
      MAX_PATHS               = tostring(var.max_paths)
      MAX_SHARDS              = tostring(var.max_shards)
      MAX_PATHS_PER_SHARD     = tostring(var.max_paths_per_shard)
      JOB_DEADLINE_S          = tostring(var.job_deadline_s)
      JOB_TTL_DAYS            = "365"
      WORKER_MEMORY_MB        = tostring(var.worker_memory_mb)
      FAULT_INJECTION_ENABLED = tostring(var.enable_fault_injection)
      LOG_LEVEL               = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api]
}

resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "AWS_IAM"
  invoke_mode        = "BUFFERED"
}

# Since October 2025 a Function URL caller needs both actions (D5, R9). Granted in the caller's identity policy.
data "aws_iam_policy_document" "invoke" {
  statement {
    sid       = "InvokeFunctionUrl"
    actions   = ["lambda:InvokeFunctionUrl"]
    resources = [local.function_arn]

    condition {
      test     = "StringEquals"
      variable = "lambda:FunctionUrlAuthType"
      values   = ["AWS_IAM"]
    }
  }

  statement {
    sid       = "InvokeViaFunctionUrl"
    actions   = ["lambda:InvokeFunction"]
    resources = [local.function_arn]

    condition {
      test     = "Bool"
      variable = "lambda:InvokedViaFunctionUrl"
      values   = ["true"]
    }
  }
}

data "aws_iam_policy_document" "client_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.account_id}:root"]
    }
  }
}

resource "aws_iam_role" "client" {
  name               = "${var.name}-client"
  assume_role_policy = data.aws_iam_policy_document.client_assume.json
}

resource "aws_iam_role_policy" "client" {
  name   = "${var.name}-invoke"
  role   = aws_iam_role.client.id
  policy = data.aws_iam_policy_document.invoke.json
}

# Grader access (D26). The access key is created by CLI so it never enters state; force_destroy lets destroy
# remove a user that has one.
resource "aws_iam_user" "grader" {
  count         = var.create_grader_user ? 1 : 0
  name          = "${var.name}-grader"
  force_destroy = true
}

# On the user, not a group: the new-AWS-experience Paid-plan SCP denies iam:CreateGroup, PutGroupPolicy and
# AddUserToGroup (DenyIAMRestrictedActions).
resource "aws_iam_user_policy" "grader" {
  #checkov:skip=CKV_AWS_40:IAM groups are denied by the project's managed SCP; the user has only this invoke policy
  count  = var.create_grader_user ? 1 : 0
  name   = "${var.name}-invoke"
  user   = aws_iam_user.grader[0].name
  policy = data.aws_iam_policy_document.invoke.json
}
