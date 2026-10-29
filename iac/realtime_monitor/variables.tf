variable "region" {
  description = "AWS region resources are deployed in."
  type        = string
  default     = "ap-northeast-1"
}

variable "memory_size" {
  description = "Lambda memory size in MB."
  type        = number
  default     = 256

  validation {
    condition     = var.memory_size >= 128 && var.memory_size <= 10240
    error_message = "memory_size must be between 128 and 10240."
  }
}

variable "timeout" {
  description = "Lambda timeout in seconds."
  type        = number
  default     = 60

  validation {
    condition     = var.timeout > 0 && var.timeout <= 900
    error_message = "timeout must be between 1 and 900."
  }
}

variable "cloudwatch_logs_retention_in_days" {
  description = "CloudWatch Logs retention period in days."
  type        = number
  default     = 365

  validation {
    condition     = var.cloudwatch_logs_retention_in_days >= 1
    error_message = "cloudwatch_logs_retention_in_days must be at least 1."
  }
}

variable "log_format" {
  description = "Lambda log format. Set to 'json' for structured JSON logs."
  type        = string
  default     = "text"

  validation {
    condition     = contains(["text", "json"], var.log_format)
    error_message = "log_format must be 'text' or 'json'."
  }
}

variable "enable_xray_tracing" {
  description = "Enable AWS X-Ray active tracing for the Lambda function."
  type        = bool
  default     = true
}

variable "create_dashboard" {
  description = "Create a CloudWatch dashboard for the stack."
  type        = bool
  default     = true
}

variable "enable_step_functions" {
  description = "Use AWS Step Functions to poll CloudWatch Logs query completion asynchronously."
  type        = bool
  default     = true
}

variable "slack_webhook_url" {
  description = "Incoming webhook URL for Slack notifications. Ignored when slack_webhook_secret_arn is set."
  type        = string
  sensitive   = true
  default     = ""
}

variable "slack_webhook_secret_arn" {
  description = "Optional Secrets Manager ARN for the Slack webhook URL. If set, takes precedence over slack_webhook_url."
  type        = string
  sensitive   = false
  default     = ""
}

variable "price_per_gb" {
  description = "CloudWatch Logs Insights price per GB scanned (USD). Must be configured explicitly because pricing varies by region and tier."
  type        = number

  validation {
    condition     = var.price_per_gb >= 0
    error_message = "price_per_gb must be >= 0."
  }
}

variable "warning_threshold_gb" {
  description = "Warning threshold for scanned GB."
  type        = number
  default     = 10
}

variable "recovery_period_in_days" {
  description = "DynamoDB retention period for metrics in days."
  type        = number
  default     = 35
}

variable "query_wait_seconds" {
  description = "Maximum wait time in seconds for CloudWatch Logs query completion."
  type        = number
  default     = 60
}

variable "query_wait_interval_seconds" {
  description = "Polling interval in seconds while waiting for query completion."
  type        = number
  default     = 1

  validation {
    condition     = var.query_wait_interval_seconds > 0
    error_message = "query_wait_interval_seconds must be greater than 0."
  }
}

variable "dynamodb_table_name" {
  description = "DynamoDB table name for scan statistics."
  type        = string
  default     = "querycost-sentinel-realtime-monitor"
}

variable "kms_key_deletion_window_in_days" {
  description = "KMS key deletion window in days."
  type        = number
  default     = 7
}

variable "dead_letter_target_arn" {
  description = "Optional ARN of an SQS queue or SNS topic for Lambda dead-letter configuration."
  type        = string
  default     = ""
}

variable "deletion_protection_enabled" {
  description = "Enable deletion protection for the DynamoDB table."
  type        = bool
  default     = true
}

variable "alarm_sns_topic_arn" {
  description = "Optional SNS topic ARN to notify when CloudWatch alarms trigger. If empty and create_alarm_sns_topic is true, a topic is created automatically."
  type        = string
  default     = ""
}

variable "create_alarm_sns_topic" {
  description = "Create an SNS topic automatically when alarm_sns_topic_arn is empty."
  type        = bool
  default     = true
}

variable "enable_anomaly_detection_alarm" {
  description = "Create a CloudWatch anomaly detection alarm for Lambda errors."
  type        = bool
  default     = true
}

variable "cost_anomaly_sigma" {
  description = "Number of standard deviations above the user's recent mean cost to flag a real-time query as anomalous. Set to 0 to disable."
  type        = number
  default     = 0

  validation {
    condition     = var.cost_anomaly_sigma >= 0
    error_message = "cost_anomaly_sigma must be >= 0."
  }
}

variable "cost_anomaly_lookback_hours" {
  description = "How many hours of the user's recent query history to consider for real-time cost anomaly detection."
  type        = number
  default     = 168

  validation {
    condition     = var.cost_anomaly_lookback_hours >= 1 && var.cost_anomaly_lookback_hours <= 720
    error_message = "cost_anomaly_lookback_hours must be between 1 and 720."
  }
}

variable "alarm_lambda_errors_threshold" {
  description = "Static threshold for Lambda error count CloudWatch alarm."
  type        = number
  default     = 0

  validation {
    condition     = var.alarm_lambda_errors_threshold >= 0
    error_message = "alarm_lambda_errors_threshold must be >= 0."
  }
}

variable "alarm_lambda_errors_evaluation_periods" {
  description = "Number of evaluation periods for the Lambda error count alarm."
  type        = number
  default     = 1

  validation {
    condition     = var.alarm_lambda_errors_evaluation_periods >= 1
    error_message = "alarm_lambda_errors_evaluation_periods must be >= 1."
  }
}

variable "alarm_dynamodb_throttles_threshold" {
  description = "Static threshold for DynamoDB throttle count CloudWatch alarm."
  type        = number
  default     = 0

  validation {
    condition     = var.alarm_dynamodb_throttles_threshold >= 0
    error_message = "alarm_dynamodb_throttles_threshold must be >= 0."
  }
}

variable "alarm_dynamodb_throttles_evaluation_periods" {
  description = "Number of evaluation periods for the DynamoDB throttle count alarm."
  type        = number
  default     = 1

  validation {
    condition     = var.alarm_dynamodb_throttles_evaluation_periods >= 1
    error_message = "alarm_dynamodb_throttles_evaluation_periods must be >= 1."
  }
}

variable "alarm_anomaly_band" {
  description = "Width of the CloudWatch anomaly detection band in standard deviations."
  type        = number
  default     = 2

  validation {
    condition     = var.alarm_anomaly_band >= 1 && var.alarm_anomaly_band <= 10
    error_message = "alarm_anomaly_band must be between 1 and 10."
  }
}
