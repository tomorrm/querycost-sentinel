resource "aws_cloudwatch_event_rule" "this" {
  name                = "${local.name}-schedule"
  description         = "Run monthly CloudWatch Logs ranking generation."
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "this" {
  rule      = aws_cloudwatch_event_rule.this.name
  target_id = "${local.name}-monthly-ranking"
  arn       = aws_lambda_function.this.arn
}
