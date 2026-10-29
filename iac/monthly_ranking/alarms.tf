locals {
  alarm_sns_topic_arn = var.alarm_sns_topic_arn != "" ? var.alarm_sns_topic_arn : (
    var.alarm_sns_topic_arn == "" && var.create_alarm_sns_topic ? aws_sns_topic.alarm[0].arn : ""
  )
}

resource "aws_sns_topic" "alarm" {
  count = var.alarm_sns_topic_arn == "" && var.create_alarm_sns_topic ? 1 : 0

  name              = "${local.name}-alarms"
  kms_master_key_id = "alias/aws/sns"
  tags              = local.tags
}

data "aws_iam_policy_document" "alarm_sns_topic" {
  count = var.alarm_sns_topic_arn == "" && var.create_alarm_sns_topic ? 1 : 0

  statement {
    sid    = "AllowCloudWatchPublish"
    effect = "Allow"

    actions = ["SNS:Publish"]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    resources = [aws_sns_topic.alarm[0].arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alarm" {
  count = var.alarm_sns_topic_arn == "" && var.create_alarm_sns_topic ? 1 : 0

  arn    = aws_sns_topic.alarm[0].arn
  policy = data.aws_iam_policy_document.alarm_sns_topic[0].json
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  count = local.alarm_sns_topic_arn != "" ? 1 : 0

  alarm_name          = "${local.name}-lambda-errors"
  alarm_description   = "Detect errors in the monthly ranking Lambda function."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = var.alarm_lambda_errors_evaluation_periods
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = var.alarm_lambda_errors_threshold
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.this.function_name
  }

  alarm_actions = [local.alarm_sns_topic_arn]
  ok_actions    = [local.alarm_sns_topic_arn]
  tags          = local.tags
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors_anomaly" {
  count = local.alarm_sns_topic_arn != "" && var.enable_anomaly_detection_alarm ? 1 : 0

  alarm_name          = "${local.name}-lambda-errors-anomaly"
  alarm_description   = "Detect anomalous error rates in the monthly ranking Lambda function."
  comparison_operator = "GreaterThanUpperThreshold"
  evaluation_periods  = var.alarm_lambda_errors_evaluation_periods
  threshold_metric_id = "ad1"
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "ad1"
    expression  = "ANOMALY_DETECTION_BAND(m1, ${var.alarm_anomaly_band})"
    label       = "Error count anomaly band"
    return_data = true
  }

  metric_query {
    id = "m1"
    metric {
      metric_name = "Errors"
      namespace   = "AWS/Lambda"
      period      = 300
      stat        = "Sum"

      dimensions = {
        FunctionName = aws_lambda_function.this.function_name
      }
    }
  }

  alarm_actions = [local.alarm_sns_topic_arn]
  ok_actions    = [local.alarm_sns_topic_arn]
  tags          = local.tags
}
