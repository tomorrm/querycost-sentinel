resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name}-lambda"
  retention_in_days = var.cloudwatch_logs_retention_in_days
  kms_key_id        = aws_kms_key.this.arn
  tags              = local.tags
}

data "archive_file" "lambda_package" {
  type        = "zip"
  source_dir  = "${path.module}/../../script/realtime_monitor/src"
  output_path = "${path.module}/package/realtime-monitor-lambda.zip"
}

resource "aws_lambda_function" "this" {
  #checkov:skip=CKV_AWS_50: X-Ray tracing is configurable via the enable_xray_tracing variable.
  #checkov:skip=CKV_AWS_117: This Lambda does not access VPC-only resources.
  #checkov:skip=CKV_AWS_272: Code signing is not required for this workload.
  #checkov:skip=CKV_AWS_115: Reserved concurrency is not required.
  function_name    = "${local.name}-lambda"
  role             = aws_iam_role.this.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.lambda_package.output_path
  source_code_hash = data.archive_file.lambda_package.output_base64sha256
  kms_key_arn      = aws_kms_key.this.arn

  timeout       = var.timeout
  memory_size   = var.memory_size
  architectures = ["x86_64"]
  publish       = true

  dynamic "dead_letter_config" {
    for_each = var.dead_letter_target_arn != "" ? [var.dead_letter_target_arn] : []

    content {
      target_arn = dead_letter_config.value
    }
  }

  dynamic "tracing_config" {
    for_each = var.enable_xray_tracing ? [1] : []

    content {
      mode = "Active"
    }
  }

  environment {
    variables = {
      SLACK_WEBHOOK_URL                = var.slack_webhook_url
      SLACK_WEBHOOK_SECRET_ARN         = var.slack_webhook_secret_arn
      DYNAMODB_TABLE_NAME              = aws_dynamodb_table.this.name
      PRICE_PER_GB                     = tostring(var.price_per_gb)
      WARNING_THRESHOLD_GB             = tostring(var.warning_threshold_gb)
      QUERY_WAIT_SECONDS               = tostring(var.query_wait_seconds)
      QUERY_WAIT_INTERVAL_SECONDS      = tostring(var.query_wait_interval_seconds)
      RECOVERY_PERIOD_DAYS             = tostring(var.recovery_period_in_days)
      AWS_REGION                       = var.region
      STEP_FUNCTIONS_STATE_MACHINE_ARN = var.enable_step_functions ? "arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:stateMachine:${local.name}-query-waiter" : ""
      COST_ANOMALY_SIGMA               = tostring(var.cost_anomaly_sigma)
      COST_ANOMALY_LOOKBACK_HOURS      = tostring(var.cost_anomaly_lookback_hours)
      LOG_FORMAT                       = var.log_format
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.basic_execution,
    aws_iam_role_policy_attachment.xray,
    aws_iam_role_policy.this,
    aws_cloudwatch_log_group.lambda,
  ]
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowExecutionFromCloudWatchEvents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.this.arn
}
