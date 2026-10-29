data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = "${local.name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = local.tags
}

data "aws_iam_policy_document" "lambda" {
  #checkov:skip=CKV_AWS_356: CloudWatch Logs GetQueryResults/DescribeQueries do not support resource-level permissions.
  statement {
    sid    = "LogsRead"
    effect = "Allow"

    actions = [
      "logs:GetQueryResults",
      "logs:DescribeQueries",
    ]

    resources = ["*"]
  }

  statement {
    sid    = "DynamodbWriteAndReadAnomaly"
    effect = "Allow"

    actions = [
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]

    resources = [
      aws_dynamodb_table.this.arn,
      "${aws_dynamodb_table.this.arn}/*",
    ]
  }

  statement {
    sid    = "KmsForEncryptedStorage"
    effect = "Allow"

    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
      "kms:DescribeKey",
    ]

    resources = [aws_kms_key.this.arn]
  }

  dynamic "statement" {
    for_each = var.slack_webhook_secret_arn != "" ? [var.slack_webhook_secret_arn] : []

    content {
      sid    = "SecretsManagerReadSlackWebhook"
      effect = "Allow"

      actions = [
        "secretsmanager:GetSecretValue",
      ]

      resources = [statement.value]
    }
  }

  dynamic "statement" {
    for_each = var.enable_step_functions ? [1] : []

    content {
      sid    = "StartStepFunctionsExecution"
      effect = "Allow"

      actions = [
        "states:StartExecution",
      ]

      # Construct the ARN without referencing the state machine resource to avoid a dependency cycle.
      resources = ["arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:stateMachine:${local.name}-query-waiter"]
    }
  }
}

resource "aws_iam_role_policy" "this" {
  name   = "${local.name}-policy"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.lambda.json
}

resource "aws_iam_role_policy_attachment" "basic_execution" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "xray" {
  count = var.enable_xray_tracing ? 1 : 0

  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}
