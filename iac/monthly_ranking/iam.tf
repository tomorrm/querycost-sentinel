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
  statement {
    sid    = "DynamodbRead"
    effect = "Allow"

    actions = [
      "dynamodb:Query",
      "dynamodb:Scan",
    ]

    resources = [
      data.aws_dynamodb_table.source.arn,
      "${data.aws_dynamodb_table.source.arn}/index/*",
    ]
  }

  dynamic "statement" {
    for_each = var.kms_key_arn != "" ? [var.kms_key_arn] : []

    content {
      sid    = "KmsDecryptForSourceTable"
      effect = "Allow"

      actions = [
        "kms:Decrypt",
      ]

      resources = [statement.value]
    }
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
