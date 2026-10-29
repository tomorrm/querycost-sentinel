resource "aws_cloudwatch_event_rule" "this" {
  name        = "${local.name}-startquery-rule"
  description = "Invoke realtime monitor when CloudTrail logs StartQuery events."

  event_pattern = jsonencode({
    source      = ["aws.logs"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventSource = ["logs.amazonaws.com"]
      eventName   = ["StartQuery"]
    }
  })
}

resource "aws_cloudwatch_event_target" "this" {
  rule       = aws_cloudwatch_event_rule.this.name
  target_id  = "${local.name}-realtime-monitor"
  arn        = aws_lambda_function.this.arn
  input_path = "$.detail"
}
