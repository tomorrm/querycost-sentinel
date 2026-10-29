# QueryCost Sentinel

[![release-readiness](https://github.com/tomorrm/querycost-sentinel/actions/workflows/release-readiness.yml/badge.svg)](https://github.com/tomorrm/querycost-sentinel/actions/workflows/release-readiness.yml)
[![pages](https://github.com/tomorrm/querycost-sentinel/actions/workflows/pages.yml/badge.svg)](https://github.com/tomorrm/querycost-sentinel/actions/workflows/pages.yml)

[Live site](https://tomorrm.github.io/querycost-sentinel/)

A serverless operations foundation that monitors CloudWatch Logs `StartQuery` usage and sends monthly per-user aggregation reports to Slack.

## GitHub Pages setup

The workflow in `.github/workflows/pages.yml` publishes the static site from `docs/`.
Before the first deployment, a maintainer with permission to manage Pages settings
must open **Settings → Pages → Build and deployment** and select **GitHub Actions**
as the source. Review `docs/` before enabling publication: Pages sites are generally
public, even when the repository is private.

If `actions/configure-pages` fails with `Get Pages site failed` and HTTP 404:

1. Confirm that the account inspecting the repository has access. A 404 from the
   repository API as well as the Pages API does not establish that Pages is disabled.
2. Confirm that Pages is enabled and its source is **GitHub Actions**. With an
   authorized account, `gh api repos/tomorrm/querycost-sentinel/pages --jq .build_type`
   should return `workflow`.
3. After correcting the settings, rerun **Deploy GitHub Pages** from the Actions tab.

The workflow intentionally does not auto-enable Pages or use a personal access
token. `actions/configure-pages@v6.0.0` requires a token other than `GITHUB_TOKEN`
for `enablement: true`: its documented requirements are `repo` scope or Pages write
permission for a personal access token, or `administration:write` and `pages:write`
for a GitHub App. Adding ordinary workflow permissions alone does not satisfy this
requirement.

## Features

- **Real-time monitoring** (`realtime_monitor`):
  - Captures CloudWatch Logs `StartQuery` events via EventBridge.
  - Optionally polls query completion through AWS Step Functions for reliability.
  - Records scan statistics in DynamoDB with TTL-based retention.
  - Sends a Slack notification only when the scanned volume exceeds a configurable threshold.
  - Detects per-user cost anomalies against recent query history.

- **Monthly ranking** (`monthly_ranking`):
  - Triggered periodically by EventBridge (default: 3rd day of each month at 09:00 UTC).
  - Aggregates per-user `ScannedGB` and estimated cost for the current month.
  - Publishes a top-N ranking to Slack with optional cost anomaly highlights.
  - Supports Japanese (`ja`) and English (`en`) notification messages.

- **Infrastructure as code**:
  - Terraform modules under `iac/` provision Lambda, IAM, DynamoDB, EventBridge, KMS, CloudWatch Logs, CloudWatch alarms, dashboards, and SNS topics.

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the detailed data flow, data model, and operational metrics.

```text
CloudTrail
└─ EventBridge Rule (StartQuery)
   └─ Lambda A: realtime_monitor
      ├─ logs:GetQueryResults / logs:DescribeQueries
      │   (or Step Functions polling)
      ├─ DynamoDB PutItem
      ├─ Slack Webhook (optional)
      └─ CloudWatch Metrics / Alarms / X-Ray

EventBridge (cron)
└─ Lambda B: monthly_ranking
   ├─ DynamoDB Query via YearMonthIndex
   ├─ per-user aggregation
   ├─ Slack Webhook (optional)
   └─ CloudWatch Metrics / Alarms / X-Ray
```

## Repository Layout

```text
.
├── iac/
│   ├── realtime_monitor/    # Terraform stack for real-time monitoring
│   └── monthly_ranking/     # Terraform stack for monthly ranking
├── script/
│   ├── realtime_monitor/    # Lambda source and tests
│   └── monthly_ranking/     # Lambda source and tests
├── ARCHITECTURE.md          # System design and data flow
├── SECURITY.md              # Security controls and incident response
├── SUPPORT.md               # How to ask questions and report issues
├── CONTRIBUTING.md          # Contribution guidelines
├── CODE_OF_CONDUCT.md       # Community standards
├── CHANGELOG.md             # Release notes
├── LICENSE                  # MIT License
└── README.md                # This file
```

## Prerequisites

- Terraform `>= 1.6`
- Authenticated AWS CLI with deployment privileges for the target account
- CloudTrail configured to deliver management events to Amazon EventBridge in the target region
- Python `3.12`
- `git`
- `mise` + `uv` (recommended) or any Python virtual environment tool

## Quick Start

1. Clone the repository.
2. Deploy the **real-time monitoring** stack first. It creates the DynamoDB table that the monthly ranking stack reads.

```bash
cd iac/realtime_monitor
terraform init
terraform plan -var="slack_webhook_url=https://example.com/YOUR/WEBHOOK/URL" -var="price_per_gb=0.005"
terraform apply -var="slack_webhook_url=https://example.com/YOUR/WEBHOOK/URL" -var="price_per_gb=0.005"
```

You can also copy `terraform.tfvars.example` to `terraform.tfvars`, fill in your values, and run `terraform apply`. To avoid passing the Slack webhook URL in plain text, store it in AWS Secrets Manager and pass the ARN via `-var="slack_webhook_secret_arn=arn:aws:secretsmanager:..."`.

3. Deploy the **monthly ranking** stack:

```bash
cd ../monthly_ranking
terraform init
terraform plan -var="slack_webhook_url=https://example.com/YOUR/WEBHOOK/URL" -var="dynamodb_table_name=querycost-sentinel-realtime-monitor"
terraform apply -var="slack_webhook_url=https://example.com/YOUR/WEBHOOK/URL" -var="dynamodb_table_name=querycost-sentinel-realtime-monitor"
```

If the source DynamoDB table is encrypted with a customer managed KMS key, pass the key ARN to grant the monthly ranking Lambda decrypt access: `-var="kms_key_arn=arn:aws:kms:..."`.

For production deployments, configure a remote Terraform backend by copying `iac/backend.tf.example` to `iac/realtime_monitor/backend.tf` and `iac/monthly_ranking/backend.tf` before running `terraform init`.

Both stacks can be configured using the variables described below.

## Configuration

### Real-time Monitoring (`realtime_monitor`)

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `SLACK_WEBHOOK_URL` | No | `""` | Slack incoming webhook URL. Empty string disables notifications. Ignored when `SLACK_WEBHOOK_SECRET_ARN` is set. |
| `SLACK_WEBHOOK_SECRET_ARN` | No | `""` | Optional Secrets Manager ARN for the Slack webhook URL. If set, takes precedence over `SLACK_WEBHOOK_URL`. |
| `DYNAMODB_TABLE_NAME` | Yes | — | DynamoDB table for storing scan records. |
| `PRICE_PER_GB` | Yes | — | CloudWatch Logs Insights price per GB scanned (USD). Must be configured explicitly because pricing varies by region and tier. |
| `WARNING_THRESHOLD_GB` | No | `10` | Threshold in GB that triggers a warning notification. |
| `QUERY_WAIT_SECONDS` | No | `60` | Maximum time to wait for a CloudWatch Logs query to complete (1–900). |
| `QUERY_WAIT_INTERVAL_SECONDS` | No | `1` | Polling interval while waiting for query completion (1–60). |
| `RECOVERY_PERIOD_DAYS` | No | `35` | DynamoDB TTL / retention period in days (1–365). |
| `AWS_REGION` | No | `ap-northeast-1` | AWS region used by the Lambda runtime. |
| `LOG_FORMAT` | No | `text` | Set to `json` to emit structured JSON logs for CloudWatch Logs Insights. |
| `COST_ANOMALY_SIGMA` | No | `0` | Flag a query whose estimated cost exceeds the user's recent mean + N * stddev. Set `0` to disable. |
| `COST_ANOMALY_LOOKBACK_HOURS` | No | `168` | Hours of the user's recent history used for real-time cost anomaly detection (1–720). |

### Monthly Ranking (`monthly_ranking`)

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `SLACK_WEBHOOK_URL` | No | `""` | Slack incoming webhook URL. Empty string disables notifications. Ignored when `SLACK_WEBHOOK_SECRET_ARN` is set. |
| `SLACK_WEBHOOK_SECRET_ARN` | No | `""` | Optional Secrets Manager ARN for the Slack webhook URL. If set, takes precedence over `SLACK_WEBHOOK_URL`. |
| `DYNAMODB_TABLE_NAME` | Yes | — | DynamoDB table that stores real-time scan records. |
| `AWS_PAGINATION_LIMIT` | No | `100` | DynamoDB page size when querying/scanning (1–1000). |
| `TOP_N` | No | `10` | Number of users to include in the ranking (1–100). |
| `DYNAMODB_YEAR_MONTH_INDEX` | No | `YearMonthIndex` | GSI name for `YearMonth` lookups. |
| `AWS_REGION` | No | `ap-northeast-1` | AWS region used by the Lambda runtime. |
| `LOG_FORMAT` | No | `text` | Set to `json` to emit structured JSON logs for CloudWatch Logs Insights. |
| `COST_ANOMALY_SIGMA` | No | `2.0` | Flag users whose monthly cost exceeds mean + N * stddev. Set `0` to disable. |
| `NOTIFICATION_LANGUAGE` | No | `ja` | Language for Slack notifications (`ja` or `en`). |

### Optional Terraform Variables

The following Terraform-level variables are available in addition to the Lambda environment variables above:

| Variable | Stack | Default | Description |
| --- | --- | --- | --- |
| `slack_webhook_secret_arn` | both | `""` | Use Secrets Manager for the Slack webhook URL instead of plain text. |
| `kms_key_arn` | monthly_ranking | `""` | Grant the monthly Lambda `kms:Decrypt` access to the source table's CMK. |
| `dead_letter_target_arn` | both | `""` | SQS/SNS ARN for Lambda dead-letter configuration. |
| `alarm_sns_topic_arn` | both | `""` | SNS topic ARN to notify on CloudWatch alarms. If empty, no alarms are created. |
| `deletion_protection_enabled` | realtime_monitor | `true` | Enable DynamoDB table deletion protection. |
| `enable_xray_tracing` | both | `true` | Enable AWS X-Ray active tracing on the Lambda function. |
| `create_dashboard` | both | `true` | Create a CloudWatch dashboard for the stack. |
| `cloudwatch_logs_retention_in_days` | both | `365` | CloudWatch Logs retention period in days. |
| `cost_anomaly_sigma` | monthly_ranking | `2.0` | Number of standard deviations above the mean cost to flag as an anomaly. |
| `enable_step_functions` | realtime_monitor | `true` | Use Step Functions to poll CloudWatch Logs query completion asynchronously. |
| `notification_language` | monthly_ranking | `ja` | Language for Slack notifications (`ja` or `en`). |
| `enable_anomaly_detection_alarm` | both | `true` | Create a CloudWatch anomaly detection alarm for Lambda errors. |
| `create_alarm_sns_topic` | both | `true` | Create an SNS topic automatically when `alarm_sns_topic_arn` is empty. |
| `log_format` | both | `text` | Lambda log format. Set to `json` for structured JSON logs. |
| `region` | both | `ap-northeast-1` | AWS region for the stack. |
| `memory_size` | both | `256` | Lambda memory size in MB. |
| `timeout` | both | `60` | Lambda timeout in seconds. |
| `kms_key_deletion_window_in_days` | realtime_monitor | `7` | KMS key deletion window in days. |
| `cost_anomaly_sigma` | realtime_monitor | `0` | Standard deviations above the user's recent mean cost to flag a real-time query as anomalous. |
| `cost_anomaly_lookback_hours` | realtime_monitor | `168` | Hours of the user's recent query history for real-time cost anomaly detection. |
| `alarm_lambda_errors_threshold` | both | `0` | Static threshold for Lambda error count alarm. |
| `alarm_lambda_errors_evaluation_periods` | both | `1` | Number of evaluation periods for the Lambda error count alarm. |
| `alarm_dynamodb_throttles_threshold` | realtime_monitor | `0` | Static threshold for DynamoDB write throttle alarm. |
| `alarm_dynamodb_throttles_evaluation_periods` | realtime_monitor | `1` | Number of evaluation periods for the DynamoDB throttle alarm. |
| `alarm_anomaly_band` | both | `2` | Width of the CloudWatch anomaly detection band in standard deviations. |
| `dynamodb_table_name` | both | see stack | DynamoDB table name (realtime: `querycost-sentinel-realtime-monitor`). |
| `dynamodb_year_month_index` | monthly_ranking | `YearMonthIndex` | GSI name for `YearMonth` lookups. |
| `schedule_expression` | monthly_ranking | `cron(0 9 3 * ? *)` | EventBridge schedule expression for monthly ranking. |
| `top_n` | monthly_ranking | `10` | Number of users to include in the ranking (1–100). |
| `aws_pagination_limit` | monthly_ranking | `100` | DynamoDB page size when querying/scanning (1–1000). |

See `iac/*/terraform.tfvars.example` for usage examples.

## Development and Testing

### Install Dependencies

```bash
cd script/realtime_monitor
mise exec -- uv venv .venv
mise exec -- uv pip install -r requirements.txt
mise exec -- uv pip install -r requirements-test.txt

cd ../monthly_ranking
mise exec -- uv venv .venv
mise exec -- uv pip install -r requirements.txt
mise exec -- uv pip install -r requirements-test.txt
```

### Syntax Check

```bash
cd script/realtime_monitor
./.venv/bin/python -m py_compile src/lambda_function.py src/lambda_exceptions.py

cd ../monthly_ranking
./.venv/bin/python -m py_compile src/lambda_function.py src/lambda_exceptions.py
```

### Run Tests

```bash
cd script/realtime_monitor
PYTHONPATH=src ./.venv/bin/python -m pytest tests

cd ../monthly_ranking
PYTHONPATH=src ./.venv/bin/python -m pytest tests
```

### Validate Terraform

```bash
cd iac/realtime_monitor
terraform init
terraform fmt -check -recursive
terraform plan

cd ../monthly_ranking
terraform init
terraform fmt -check -recursive
terraform plan
```

The `release-readiness` GitHub Actions workflow runs Terraform validation and Lambda unit tests on every push and pull request.

## Production Checklist

### Real-time Monitoring

- `QUERY_WAIT_SECONDS` is smaller than the Lambda timeout.
- `QUERY_WAIT_INTERVAL_SECONDS <= QUERY_WAIT_SECONDS`.
- `PRICE_PER_GB`, `WARNING_THRESHOLD_GB`, `RECOVERY_PERIOD_DAYS`, and `QUERY_WAIT_*` values are within expected ranges.
- Slack URL is a valid HTTPS URL or empty.
- `DYNAMODB_TABLE_NAME` and `AWS_REGION` are correct.

### Monthly Ranking

- `AWS_PAGINATION_LIMIT` and `TOP_N` are within valid ranges.
- `DYNAMODB_YEAR_MONTH_INDEX` matches the deployed GSI name.
- Slack URL is HTTPS or empty.

## Operations Notes

- Monitor `realtime_monitor` for `Invocations`, `Errors`, `Duration`, and `Throttles`.
- Monitor `monthly_ranking` for success rate and execution duration.
- Monitor DynamoDB for `ConsumedWriteCapacityUnits`, `ConsumedReadCapacityUnits`, and `Throttles`.
- Audit and rotate `SLACK_WEBHOOK_URL` regularly; it is treated as sensitive configuration.

## Data Privacy and Retention

QueryCost Sentinel stores the following information in DynamoDB to aggregate usage:

- IAM user or session name that invoked `StartQuery`
- CloudWatch Logs query string (truncated to 500 characters)
- Scanned bytes, estimated cost, and metadata such as log group names

This data is encrypted at rest with the KMS key created by the `realtime_monitor` stack and is automatically deleted by DynamoDB TTL after the configured `RECOVERY_PERIOD_DAYS`. Review this retention period against your organization's privacy and compliance requirements before deploying to production.

## Security

See [SECURITY.md](./SECURITY.md) for the security policy, supported versions, vulnerability reporting process, and operational incident response.

## Support

See [SUPPORT.md](./SUPPORT.md) for how to ask questions, report bugs, and request features.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for setup instructions, coding standards, and the pull request process.

## Code of Conduct

See [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md).

## License

QueryCost Sentinel is released under the [MIT License](./LICENSE).
