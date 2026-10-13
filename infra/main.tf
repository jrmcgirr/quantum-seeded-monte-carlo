locals {
  name              = "qmc-${var.env}"
  metrics_namespace = "QMC/${var.env}"
  # Built as a string: never the SSM parameter data source, which writes the decrypted token into state (I6).
  qrypt_param_arn = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${var.qrypt_api_key_param_name}"
}

module "data" {
  source = "./modules/data"

  name                 = local.name
  table_rcu            = var.table_rcu
  table_wcu            = var.table_wcu
  visibility_timeout_s = var.visibility_timeout_s
  max_receive_count    = var.max_receive_count
}

module "workers" {
  source = "./modules/workers"

  name                   = local.name
  env                    = var.env
  build_sha              = var.build_sha
  zip_path               = var.worker_zip
  memory_mb              = var.worker_memory_mb
  timeout_s              = var.worker_timeout_s
  reserved_concurrency   = var.worker_reserved_concurrency
  max_concurrency        = var.worker_max_concurrency
  log_retention_days     = var.log_retention_days
  table_arn              = module.data.table_arn
  table_name             = module.data.table_name
  queue_arn              = module.data.queue_arn
  queue_url              = module.data.queue_url
  metrics_namespace      = local.metrics_namespace
  max_receive_count      = var.max_receive_count
  enable_fault_injection = var.enable_fault_injection
}

module "api" {
  source = "./modules/api"

  name                   = local.name
  env                    = var.env
  build_sha              = var.build_sha
  zip_path               = var.api_zip
  memory_mb              = var.api_memory_mb
  log_retention_days     = var.log_retention_days
  account_id             = data.aws_caller_identity.current.account_id
  table_arn              = module.data.table_arn
  table_name             = module.data.table_name
  queue_arn              = module.data.queue_arn
  queue_url              = module.data.queue_url
  metrics_namespace      = local.metrics_namespace
  qrypt_param_name       = var.qrypt_api_key_param_name
  qrypt_param_arn        = local.qrypt_param_arn
  qrypt_max_per_window   = var.qrypt_max_per_window
  max_paths              = var.max_paths
  max_shards             = var.max_shards
  max_paths_per_shard    = var.max_paths_per_shard
  job_deadline_s         = var.job_deadline_s
  worker_memory_mb       = var.worker_memory_mb
  enable_fault_injection = var.enable_fault_injection
  create_grader_user     = var.create_grader_user
}

module "observability" {
  source = "./modules/observability"

  name              = local.name
  account_id        = data.aws_caller_identity.current.account_id
  alert_email       = var.alert_email
  metrics_namespace = local.metrics_namespace
  queue_name        = module.data.queue_name
  dlq_name          = module.data.dlq_name
  worker_function   = module.workers.function_name
  api_function      = module.api.function_name
}

module "budget" {
  source = "./modules/budget"
  count  = var.create_budget ? 1 : 0

  limit_usd = var.budget_limit_usd
  # This ARN comes from the topic policy resource, so the Budget waits for the publish permission it validates.
  topic_arn   = module.observability.alerts_topic_arn
  alert_email = var.alert_email
}
