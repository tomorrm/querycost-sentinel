# Security Policy

## Supported Versions

Only the latest commit on the default branch is supported with security updates. Because QueryCost Sentinel is deployed as infrastructure rather than a library, apply fixes by redeploying the affected Terraform stack.

| Version   | Supported          |
| --------- | ------------------ |
| `main`    | :white_check_mark: |
| `< main`  | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability, please do not open a public issue. Instead, report it privately via [GitHub Security Advisories](https://github.com/tomorrm/querycost-sentinel/security/advisories/new) so we can investigate and coordinate a fix before disclosure.

When reporting, include:

- A description of the vulnerability and its impact.
- Steps to reproduce or proof-of-concept code.
- Affected versions, files, or configuration values.
- Suggested remediation if you have one.

We aim to respond within 5 business days and will keep you informed throughout the remediation process.

## Security Controls

### Configuration Guards

The real-time Lambda validates the following at startup:

- `QUERY_WAIT_SECONDS` (1–900)
- `QUERY_WAIT_INTERVAL_SECONDS` (1–60)
- `RECOVERY_PERIOD_DAYS` (1–365)
- `PRICE_PER_GB`, `WARNING_THRESHOLD_GB` (non-negative with reasonable upper bounds)
- `COST_ANOMALY_SIGMA` (0–10) and `COST_ANOMALY_LOOKBACK_HOURS` (1–720)
- `SLACK_WEBHOOK_URL` (empty string or HTTPS URL)

The monthly Lambda also restricts `SLACK_WEBHOOK_URL` to empty string or HTTPS, validates `TOP_N` (1–100), `AWS_PAGINATION_LIMIT` (1–1000), `COST_ANOMALY_SIGMA` (≥ 0), and `NOTIFICATION_LANGUAGE` (`ja` or `en`).

### Least-Privilege IAM

#### Real-time Lambda

- `logs:GetQueryResults`
- `logs:DescribeQueries`
- `dynamodb:PutItem`
- `dynamodb:Query` on the `UserNameIndex` GSI (for cost anomaly detection)
- `dynamodb:Scan` (fallback / test paths)
- `states:StartExecution` (when Step Functions polling is enabled)
- `secretsmanager:GetSecretValue` (when `SLACK_WEBHOOK_SECRET_ARN` is used)
- `kms:Decrypt`, `kms:GenerateDataKey`, `kms:DescribeKey` on the stack KMS key

#### Monthly Ranking Lambda

- `dynamodb:Query` on the `YearMonthIndex` GSI
- `dynamodb:Scan` (fallback when the GSI is unavailable)
- `secretsmanager:GetSecretValue` (when `SLACK_WEBHOOK_SECRET_ARN` is used)
- `kms:Decrypt` on the source table KMS key (when `kms_key_arn` is provided)

#### Step Functions State Machine

- `logs:GetQueryResults`
- `lambda:InvokeFunction`
- CloudWatch Logs delivery and write permissions for execution history
- X-Ray write permissions (when X-Ray tracing is enabled)

### Audit and Monitoring

- Review Terraform / IAM diffs before applying to production.
- Check dependency update advisories regularly.
- Monitor for:
  - Sudden increases in runtime errors.
  - Internal errors caused by reaching `QUERY_WAIT_SECONDS`.
  - DynamoDB write failures.
  - Slack notification send failures.

## Incident Response

### Slack notifications stop

1. Verify `SLACK_WEBHOOK_URL` is valid (not empty, not HTTP).
2. Check Lambda exception logs and reconfirm `QUERY_WAIT_*` and `RECOVERY_PERIOD_*` values.
3. Temporarily set `SLACK_WEBHOOK_URL=""` to suppress alerts while investigating.

### Real-time records are missing

1. Confirm the `eventName` filter and `GetQueryResults` permissions.
2. Check for increases in `GetQueryResults` errors.
3. Review Lambda timeouts and DynamoDB write errors.
4. Recreate the EventBridge rule and revalidate CloudTrail ingestion if needed.

### Monthly ranking cannot be retrieved

1. Confirm `DYNAMODB_YEAR_MONTH_INDEX` matches the actual GSI.
2. Check whether a Scan fallback is being triggered due to GSI unavailability.
3. Review DynamoDB read capacity and access logs; adjust capacity if necessary.
