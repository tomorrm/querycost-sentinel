resource "aws_cloudwatch_log_group" "lambda" {
  #checkov:skip=CKV_AWS_158: Log group encryption is optional; supply a KMS key via kms_key_arn if required.
  name              = "/aws/lambda/${local.name}-lambda"
  retention_in_days = var.cloudwatch_logs_retention_in_days
  kms_key_id        = var.kms_key_arn != "" ? var.kms_key_arn : null
  tags              = local.tags
}

resource "aws_lambda_function" "this" {
  #checkov:skip=CKV_AWS_50: X-Ray tracing is configurable via the enable_xray_tracing variable.
  #checkov:skip=CKV_AWS_117: This Lambda does not access VPC-only resources.
  #checkov:skip=CKV_AWS_272: Code signing is not required for this workload.
  #checkov:skip=CKV_AWS_115: Reserved concurrency is not required.
  #checkov:skip=CKV_AWS_173: Environment variables are encrypted by default; supply kms_key_arn for customer-managed encryption.
  function_name    = "${local.name}-lambda"
  role             = aws_iam_role.this.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.lambda_package.output_path
  source_code_hash = data.archive_file.lambda_package.output_base64sha256
  kms_key_arn      = var.kms_key_arn != "" ? var.kms_key_arn : null

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
      DYNAMODB_TABLE_NAME       = data.aws_dynamodb_table.source.name
      AWS_REGION                = var.region
      AWS_PAGINATION_LIMIT      = tostring(var.aws_pagination_limit)
      TOP_N                     = tostring(var.top_n)
      DYNAMODB_YEAR_MONTH_INDEX = var.dynamodb_year_month_index
      SLACK_WEBHOOK_URL         = var.slack_webhook_url
      SLACK_WEBHOOK_SECRET_ARN  = var.slack_webhook_secret_arn
      COST_ANOMALY_SIGMA        = tostring(var.cost_anomaly_sigma)
      NOTIFICATION_LANGUAGE     = var.notification_language
      LOG_FORMAT                = var.log_format
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.basic_execution,
    aws_iam_role_policy_attachment.xray,
    aws_cloudwatch_log_group.lambda,
  ]
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.this.arn
}
