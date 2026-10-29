"""Integration tests using moto to mock AWS services."""

from datetime import datetime, timezone
from decimal import Decimal

import boto3
import lambda_function as _lambda_module
from lambda_function import Config, _detect_cost_anomaly_for_user, _resolve_slack_webhook_url, save_to_dynamodb
from moto import mock_aws


@mock_aws
def test_resolve_slack_webhook_url_from_secrets_manager() -> None:
    _lambda_module._resolved_slack_url = None
    region = "ap-northeast-1"
    secret_name = "querycost/slack-webhook"
    client = boto3.client("secretsmanager", region_name=region)
    response = client.create_secret(Name=secret_name, SecretString="https://example.com/secret")
    secret_arn = response["ARN"]

    resolved = _resolve_slack_webhook_url(secret_arn)

    assert resolved == "https://example.com/secret"


@mock_aws
def test_save_to_dynamodb_writes_item() -> None:
    region = "ap-northeast-1"
    table_name = "querycost-realtime-test"
    client = boto3.client("dynamodb", region_name=region)
    client.create_table(
        TableName=table_name,
        AttributeDefinitions=[{"AttributeName": "QueryId", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "QueryId", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )

    config = Config(
        slack_webhook_url="",
        dynamodb_table_name=table_name,
        price_per_gb=0.005,
        warning_threshold_gb=1.0,
        query_wait_seconds=60,
        query_wait_interval_seconds=1,
        recovery_period_days=35,
        aws_region=region,
        cost_anomaly_sigma=0.0,
        cost_anomaly_lookback_hours=168,
    )
    record = {
        "query_id": "q-123",
        "timestamp": "2025-06-19T23:51:04Z",
        "year_month": "2025-06",
        "user_name": "alice",
        "scanned_bytes": 1073741824,
        "scanned_gb": 1.0,
        "estimated_cost": 0.005,
        "log_groups": ["/aws/lambda/app"],
        "query_string": "fields @timestamp",
        "records_matched": 1,
        "records_scanned": 10,
        "is_warning": False,
    }

    save_to_dynamodb(config, record)

    dynamodb = boto3.resource("dynamodb", region_name=region)
    item = dynamodb.Table(table_name).get_item(Key={"QueryId": "q-123"})["Item"]
    assert item["UserName"] == "alice"
    assert float(item["ScannedGB"]) == 1.0
    assert item["LogGroups"] == ["/aws/lambda/app"]
    assert "TTL" in item


@mock_aws
def test_detect_cost_anomaly_for_user_flags_anomalous_cost() -> None:
    region = "ap-northeast-1"
    table_name = "querycost-realtime-anomaly-test"
    client = boto3.client("dynamodb", region_name=region)
    client.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "QueryId", "AttributeType": "S"},
            {"AttributeName": "UserName", "AttributeType": "S"},
        ],
        KeySchema=[{"AttributeName": "QueryId", "KeyType": "HASH"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "UserNameIndex",
                "KeySchema": [{"AttributeName": "UserName", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)
    now = datetime.now(timezone.utc).isoformat()
    for index, cost in enumerate([Decimal("0.1"), Decimal("0.12"), Decimal("0.11"), Decimal("0.13")]):
        table.put_item(
            Item={
                "QueryId": f"q-{index}",
                "UserName": "alice",
                "TimeStamp": now,
                "EstimatedCost": cost,
            }
        )

    config = Config(
        slack_webhook_url="",
        dynamodb_table_name=table_name,
        price_per_gb=0.005,
        warning_threshold_gb=1.0,
        query_wait_seconds=60,
        query_wait_interval_seconds=1,
        recovery_period_days=35,
        aws_region=region,
        cost_anomaly_sigma=2.0,
        cost_anomaly_lookback_hours=168,
    )

    assert _detect_cost_anomaly_for_user(config, "alice", Decimal("1.00"), dynamodb_resource=dynamodb) is True
    assert _detect_cost_anomaly_for_user(config, "alice", Decimal("0.12"), dynamodb_resource=dynamodb) is False


@mock_aws
def test_detect_cost_anomaly_for_user_disabled_when_sigma_zero() -> None:
    region = "ap-northeast-1"
    table_name = "querycost-realtime-anomaly-disabled-test"
    client = boto3.client("dynamodb", region_name=region)
    client.create_table(
        TableName=table_name,
        AttributeDefinitions=[{"AttributeName": "QueryId", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "QueryId", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )

    config = Config(
        slack_webhook_url="",
        dynamodb_table_name=table_name,
        price_per_gb=0.005,
        warning_threshold_gb=1.0,
        query_wait_seconds=60,
        query_wait_interval_seconds=1,
        recovery_period_days=35,
        aws_region=region,
        cost_anomaly_sigma=0.0,
        cost_anomaly_lookback_hours=168,
    )

    assert _detect_cost_anomaly_for_user(config, "alice", Decimal("100.00")) is False
