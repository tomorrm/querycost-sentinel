# QueryCost Sentinel Architecture

```text
CloudTrail
└─ EventBridge Rule (StartQuery)
   └─ Lambda A: realtime_monitor
      ├─ logs:GetQueryResults, logs:DescribeQueries (or Step Functions polling)
      ├─ DynamoDB PutItem (TTL, YearMonth, ScannedGB, EstimatedCost, ...)
      ├─ per-user cost anomaly detection via UserNameIndex
      ├─ Slack Webhook notification (optional)
      └─ CloudWatch Metrics / Alarms / Dashboard / X-Ray

EventBridge (cron)
└─ Lambda B: monthly_ranking
   ├─ DynamoDB Query via YearMonthIndex (preferred)
   ├─ fallback Scan when index is unavailable
   ├─ monthly per-user aggregation (scanned volume / cost)
   ├─ cost anomaly detection against monthly mean
   ├─ Slack Webhook notification (optional, multilingual)
      └─ CloudWatch Metrics / Alarms / Dashboard / X-Ray
```

## Real-time Monitoring Data Flow

1. EventBridge captures CloudTrail `StartQuery` events for CloudWatch Logs.
2. `realtime_monitor` extracts `queryId` and the executing user from the event.
3. The function either polls `logs:GetQueryResults` directly or starts a Step Functions state machine that polls asynchronously and invokes the Lambda back with the result.
4. Scan statistics are aggregated and persisted to DynamoDB.
5. The current query cost is compared against the user's recent history and flagged as an anomaly when it exceeds `mean + sigma * stddev`.
6. A Slack notification is sent only when `WARNING_THRESHOLD_GB` is exceeded; anomaly queries are highlighted with a distinct color and title.

## Monthly Ranking Data Flow

1. EventBridge triggers the Lambda on a monthly schedule (default: 3rd day at 09:00 UTC).
2. The Lambda targets the current `YearMonth` and queries DynamoDB using the GSI.
3. If the GSI is unavailable, it falls back to a table `Scan`.
4. Per-user `ScannedGB` and `EstimatedCost` are aggregated.
5. Users whose monthly cost exceeds `mean + sigma * stddev` are flagged as anomalies.
6. The top-N users are published to Slack in the configured language (`ja` or `en`).

## Primary Data Store

DynamoDB table for real-time aggregation:

- Partition key: `QueryId`
- Sort key: `TimeStamp`
- Attributes: `UserName`, `ScannedGB`, `EstimatedCost`, `YearMonth`, `TTL`, `IsWarning`
- TTL is enabled to automatically delete expired items.
- GSI `YearMonthIndex` supports monthly aggregation.
- GSI `UserNameIndex` supports user-centric lookups.

## Operational Metrics

### realtime_monitor

- `Invocations`, `Errors`, `Duration`, `Throttles`
- Lambda timeout rate (`max(Duration)`)
- CloudWatch Logs API status mismatches and retry failures

### monthly_ranking

- Scheduled execution success rate
- Number of Query/Scan calls and items returned
- Processing time per run

### DynamoDB

- WCU/RCU consumption
- Throttles
- Item size growth

## Security Design Assumptions

- `load_config` validates environment variable ranges at startup.
- `wait_for_query_completion` caps wait time by the remaining Lambda execution time to prevent overruns; asynchronous Step Functions polling avoids long-running Lambda waits.
- Slack URLs must use HTTPS and have a valid host; webhook URLs can also be loaded from AWS Secrets Manager at runtime.
- The monthly Lambda fails explicitly on unexpected errors, while allowing a GSI-to-Scan fallback for operational resilience.
- Real-time cost anomaly detection reads only the same user's recent history through the `UserNameIndex` GSI.
- CloudWatch alarms, dashboards, X-Ray tracing, and SNS topics are optional and configurable through Terraform variables.
