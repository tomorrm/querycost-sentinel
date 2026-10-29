output "lambda_function_arn" {
  description = "ARN of the monthly ranking Lambda function."
  value       = aws_lambda_function.this.arn
}

output "lambda_function_name" {
  description = "Name of the monthly ranking Lambda function."
  value       = aws_lambda_function.this.function_name
}

output "cloudwatch_log_group_name" {
  description = "Name of the monthly ranking Lambda CloudWatch log group."
  value       = aws_cloudwatch_log_group.lambda.name
}

output "cloudwatch_dashboard_name" {
  description = "Name of the CloudWatch dashboard, when created."
  value       = var.create_dashboard ? aws_cloudwatch_dashboard.this[0].dashboard_name : ""
}

output "alarm_sns_topic_arn" {
  description = "ARN of the SNS topic used for CloudWatch alarms, when created or provided."
  value       = local.alarm_sns_topic_arn
}
