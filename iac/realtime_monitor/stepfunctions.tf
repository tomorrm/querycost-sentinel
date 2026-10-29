data "aws_iam_policy_document" "step_functions_assume_role" {
  count = var.enable_step_functions ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "step_functions" {
  count = var.enable_step_functions ? 1 : 0

  name               = "${local.name}-query-waiter-role"
  assume_role_policy = data.aws_iam_policy_document.step_functions_assume_role[0].json
  tags               = local.tags
}

resource "aws_cloudwatch_log_group" "step_functions" {
  count = var.enable_step_functions ? 1 : 0

  name              = "/aws/vendedlogs/states/${local.name}-query-waiter"
  retention_in_days = var.cloudwatch_logs_retention_in_days
  kms_key_id        = aws_kms_key.this.arn
  tags              = local.tags
}

data "aws_iam_policy_document" "step_functions" {
  #checkov:skip=CKV_AWS_356: CloudWatch Logs delivery actions do not support resource-level permissions.
  count = var.enable_step_functions ? 1 : 0

  statement {
    sid    = "CloudWatchLogsGetQueryResults"
    effect = "Allow"

    actions = [
      "logs:GetQueryResults",
    ]

    resources = ["*"]
  }

  statement {
    sid    = "CloudWatchLogsDelivery"
    effect = "Allow"

    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]

    resources = ["*"]
  }

  statement {
    sid    = "CloudWatchLogsWrite"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = ["${aws_cloudwatch_log_group.step_functions[0].arn}:*"]
  }

  statement {
    sid    = "InvokeProcessorLambda"
    effect = "Allow"

    actions = [
      "lambda:InvokeFunction",
    ]

    resources = [aws_lambda_function.this.arn]
  }
}

resource "aws_iam_role_policy" "step_functions" {
  count = var.enable_step_functions ? 1 : 0

  name   = "${local.name}-query-waiter-policy"
  role   = aws_iam_role.step_functions[0].id
  policy = data.aws_iam_policy_document.step_functions[0].json
}

resource "aws_iam_role_policy_attachment" "step_functions_xray" {
  count = var.enable_step_functions && var.enable_xray_tracing ? 1 : 0

  role       = aws_iam_role.step_functions[0].name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

resource "aws_sfn_state_machine" "query_waiter" {
  count = var.enable_step_functions ? 1 : 0

  name     = "${local.name}-query-waiter"
  role_arn = aws_iam_role.step_functions[0].arn

  definition = jsonencode({
    Comment = "Poll CloudWatch Logs query completion and invoke the processor Lambda"
    StartAt = "GetQueryResults"
    States = {
      GetQueryResults = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:logs:getQueryResults"
        Parameters = {
          "queryId.$" = "$.queryId"
        }
        Next = "CheckStatus"
      }
      CheckStatus = {
        Type = "Choice"
        Choices = [
          {
            Variable     = "$.status"
            StringEquals = "Complete"
            Next         = "InvokeProcessor"
          },
          {
            Variable      = "$.status"
            StringMatches = "*ailed*"
            Next          = "QueryFailed"
          },
          {
            Variable      = "$.status"
            StringMatches = "*ancelled*"
            Next          = "QueryFailed"
          },
          {
            Variable      = "$.status"
            StringMatches = "*imeout*"
            Next          = "QueryFailed"
          }
        ]
        Default = "WaitForQuery"
      }
      WaitForQuery = {
        Type    = "Wait"
        Seconds = 1
        Next    = "GetQueryResults"
      }
      InvokeProcessor = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.this.arn
          Payload = {
            "detail.$"      = "$.detail"
            "queryResult.$" = "$"
          }
        }
        End = true
      }
      QueryFailed = {
        Type  = "Fail"
        Cause = "CloudWatch Logs query did not complete successfully"
      }
    }
  })

  logging_configuration {
    level                  = "ALL"
    include_execution_data = true
    log_destination        = aws_cloudwatch_log_group.step_functions[0].arn
  }

  tracing_configuration {
    enabled = var.enable_xray_tracing
  }

  tags = local.tags

  depends_on = [
    aws_iam_role_policy.step_functions,
    aws_iam_role_policy_attachment.step_functions_xray,
    aws_cloudwatch_log_group.step_functions,
  ]
}

resource "aws_lambda_permission" "step_functions" {
  count = var.enable_step_functions ? 1 : 0

  statement_id  = "AllowExecutionFromStepFunctions"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this.function_name
  principal     = "states.amazonaws.com"
  source_arn    = aws_sfn_state_machine.query_waiter[0].arn
}
