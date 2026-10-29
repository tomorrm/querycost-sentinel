output "lambda_function_arn" {
  description = "ARN of the realtime monitor Lambda function."
  value       = aws_lambda_function.this.arn
}

output "lambda_function_name" {
  description = "Name of the realtime monitor Lambda function."
  value       = aws_lambda_function.this.function_name
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB table storing scan statistics."
  value       = aws_dynamodb_table.this.name
}

output "dynamodb_table_arn" {
  description = "ARN of the DynamoDB table storing scan statistics."
  value       = aws_dynamodb_table.this.arn
}

output "cloudwatch_log_group_name" {
  description = "Name of the realtime monitor Lambda CloudWatch log group."
  value       = aws_cloudwatch_log_group.lambda.name
}

output "kms_key_arn" {
  description = "ARN of the KMS key used to encrypt the DynamoDB table."
  value       = aws_kms_key.this.arn
}

output "cloudwatch_dashboard_name" {
  description = "Name of the CloudWatch dashboard, when created."
  value       = var.create_dashboard ? aws_cloudwatch_dashboard.this[0].dashboard_name : ""
}

output "step_functions_state_machine_arn" {
  description = "ARN of the Step Functions state machine that polls query completion, when enabled."
  value       = var.enable_step_functions ? aws_sfn_state_machine.query_waiter[0].arn : ""
}

output "alarm_sns_topic_arn" {
  description = "ARN of the SNS topic used for CloudWatch alarms, when created or provided."
  value       = local.alarm_sns_topic_arn
}
