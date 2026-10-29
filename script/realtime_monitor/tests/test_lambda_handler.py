import json
from decimal import Decimal

from botocore.exceptions import ClientError
from lambda_function import Config, lambda_handler, send_slack_notification


def test_lambda_handler_non_startquery_returns_200(sample_event, opener_spy, fake_config, lambda_context):
    event = dict(sample_event)
    event["eventName"] = "StopQuery"
    result = lambda_handler(
        event,
        lambda_context,
        config=fake_config,
        opener=opener_spy,
    )
    assert result["statusCode"] == 200
    assert result["body"] == "Not StartQuery event."
    assert opener_spy.calls == 0


def test_lambda_handler_success_no_webhook(
    sample_event,
    make_logs_client,
    fake_dynamo_resource,
    opener_spy,
    lambda_context,
):
    bytes_scanned = int(0.5 * (1024**3))
    logs_client = make_logs_client(
        [
            {"status": "Running"},
            {
                "status": "Complete",
                "statistics": {
                    "bytesScanned": bytes_scanned,
                    "recordsMatched": 12,
                    "recordsScanned": 120,
                },
            },
        ]
    )
    config = Config(
        slack_webhook_url="",
        dynamodb_table_name="dummy-table",
        price_per_gb=0.0076,
        warning_threshold_gb=10.0,
        query_wait_seconds=60,
        query_wait_interval_seconds=1,
        recovery_period_days=35,
        aws_region="ap-northeast-1",
        cost_anomaly_sigma=0.0,
        cost_anomaly_lookback_hours=168,
    )

    result = lambda_handler(
        sample_event,
        lambda_context,
        logs_client=logs_client,
        dynamodb_resource=fake_dynamo_resource,
        opener=opener_spy,
        config=config,
    )

    assert result["statusCode"] == 200
    assert opener_spy.calls == 0
    assert fake_dynamo_resource.last_table_name == config.dynamodb_table_name
    item = fake_dynamo_resource.table.last_put_item
    assert item is not None
    assert item["QueryId"] == sample_event["additionalEventData"]["queryId"]
    assert item["UserName"] == "example.user"
    assert isinstance(item["ScannedBytes"], Decimal)
    assert item["ScannedGB"] == Decimal(str(bytes_scanned / (1024**3)))
    assert item["EstimatedCost"] == Decimal("0.0038")


def test_lambda_handler_success_with_notification(
    sample_event,
    make_logs_client,
    fake_dynamo_resource,
    opener_spy,
    lambda_context,
):
    bytes_scanned = int(2.0 * (1024**3))
    logs_client = make_logs_client(
        [
            {"status": "Running"},
            {
                "status": "Complete",
                "statistics": {
                    "bytesScanned": bytes_scanned,
                    "recordsMatched": 1,
                    "recordsScanned": 10,
                },
            },
        ]
    )
    config = Config(
        slack_webhook_url="http://localhost/webhook",
        dynamodb_table_name="dummy-table",
        price_per_gb=0.0076,
        warning_threshold_gb=1.0,
        query_wait_seconds=60,
        query_wait_interval_seconds=1,
        recovery_period_days=35,
        aws_region="ap-northeast-1",
        cost_anomaly_sigma=0.0,
        cost_anomaly_lookback_hours=168,
    )

    result = lambda_handler(
        sample_event,
        lambda_context,
        logs_client=logs_client,
        dynamodb_resource=fake_dynamo_resource,
        opener=opener_spy,
        config=config,
    )

    assert result["statusCode"] == 200
    assert opener_spy.calls == 1
    req = opener_spy.last_request
    assert req is not None
    payload = json.loads(req["kwargs"]["body"].decode("utf-8"))
    assert payload["attachments"][0]["fields"][0]["title"] == "Query ID"
    assert sample_event["additionalEventData"]["queryId"] in payload["text"] + "".join(
        str(f["value"]) for f in payload["attachments"][0]["fields"]
    )


def test_lambda_handler_success_below_threshold_skips_notification(
    sample_event,
    make_logs_client,
    fake_dynamo_resource,
    opener_spy,
    lambda_context,
):
    bytes_scanned = int(0.5 * (1024**3))
    logs_client = make_logs_client(
        [
            {"status": "Running"},
            {
                "status": "Complete",
                "statistics": {
                    "bytesScanned": bytes_scanned,
                    "recordsMatched": 1,
                    "recordsScanned": 10,
                },
            },
        ]
    )
    config = Config(
        slack_webhook_url="http://localhost/webhook",
        dynamodb_table_name="dummy-table",
        price_per_gb=0.0076,
        warning_threshold_gb=1.0,
        query_wait_seconds=60,
        query_wait_interval_seconds=1,
        recovery_period_days=35,
        aws_region="ap-northeast-1",
        cost_anomaly_sigma=0.0,
        cost_anomaly_lookback_hours=168,
    )

    result = lambda_handler(
        sample_event,
        lambda_context,
        logs_client=logs_client,
        dynamodb_resource=fake_dynamo_resource,
        opener=opener_spy,
        config=config,
    )

    assert result["statusCode"] == 200
    assert opener_spy.calls == 0
    assert fake_dynamo_resource.table.last_put_item is not None


def test_lambda_handler_cancelled_query_returns_500(
    sample_event,
    make_logs_client,
    fake_dynamo_resource,
    opener_spy,
    fake_config,
    lambda_context,
):
    logs_client = make_logs_client([{"status": "Cancelled"}])
    result = lambda_handler(
        sample_event,
        lambda_context,
        logs_client=logs_client,
        dynamodb_resource=fake_dynamo_resource,
        opener=opener_spy,
        config=fake_config,
    )
    assert result["statusCode"] == 500
    assert "Query failed" in result["body"]
    assert opener_spy.calls == 0


def test_lambda_handler_full_eventbridge_event(
    sample_event,
    make_logs_client,
    fake_dynamo_resource,
    opener_spy,
    lambda_context,
):
    bytes_scanned = int(0.5 * (1024**3))
    logs_client = make_logs_client(
        [
            {"status": "Running"},
            {
                "status": "Complete",
                "statistics": {
                    "bytesScanned": bytes_scanned,
                    "recordsMatched": 1,
                    "recordsScanned": 10,
                },
            },
        ]
    )
    config = Config(
        slack_webhook_url="http://localhost/webhook",
        dynamodb_table_name="dummy-table",
        price_per_gb=0.0076,
        warning_threshold_gb=10.0,
        query_wait_seconds=60,
        query_wait_interval_seconds=1,
        recovery_period_days=35,
        aws_region="ap-northeast-1",
        cost_anomaly_sigma=0.0,
        cost_anomaly_lookback_hours=168,
    )

    result = lambda_handler(
        {"detail": sample_event},
        lambda_context,
        logs_client=logs_client,
        dynamodb_resource=fake_dynamo_resource,
        opener=opener_spy,
        config=config,
    )

    assert result["statusCode"] == 200
    assert opener_spy.calls == 0
    assert fake_dynamo_resource.table.last_put_item is not None


def test_lambda_handler_query_failed_returns_500(
    sample_event,
    make_logs_client,
    fake_dynamo_resource,
    opener_spy,
    fake_config,
    lambda_context,
):
    logs_client = make_logs_client([{"status": "Failed"}])
    result = lambda_handler(
        sample_event,
        lambda_context,
        logs_client=logs_client,
        dynamodb_resource=fake_dynamo_resource,
        opener=opener_spy,
        config=fake_config,
    )
    assert result["statusCode"] == 500
    assert "Query failed" in result["body"]
    assert opener_spy.calls == 0


def test_lambda_handler_raises_on_malformed_event(lambda_context):
    result = lambda_handler(
        {},
        lambda_context,
        config=Config(
            slack_webhook_url="http://localhost/webhook",
            dynamodb_table_name="dummy-table",
            price_per_gb=0.0076,
            warning_threshold_gb=1.0,
            query_wait_seconds=60,
            query_wait_interval_seconds=1,
            recovery_period_days=35,
            aws_region="ap-northeast-1",
            cost_anomaly_sigma=0.0,
            cost_anomaly_lookback_hours=168,
        ),
    )
    assert result["statusCode"] == 500
    assert "eventName required" in result["body"]


def test_lambda_handler_dynamodb_failure_returns_500(
    sample_event,
    make_logs_client,
    opener_spy,
    fake_config,
    lambda_context,
):
    class ExplodingTable:
        def put_item(self, *, Item: dict[str, object]) -> dict[str, object]:
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem")

    class ExplodingResource:
        def Table(self, _: str) -> ExplodingTable:
            return ExplodingTable()

    logs_client = make_logs_client(
        [
            {"status": "Running"},
            {"status": "Complete", "statistics": {"bytesScanned": 0}},
        ]
    )
    result = lambda_handler(
        sample_event,
        lambda_context,
        logs_client=logs_client,
        dynamodb_resource=ExplodingResource(),
        opener=opener_spy,
        config=fake_config,
    )
    assert result["statusCode"] == 500
    assert "DynamoDB" in result["body"]
    assert opener_spy.calls == 0


def test_lambda_handler_unexpected_error_returns_500(sample_event, fake_config):
    class BoomContext:
        def get_remaining_time_in_millis(self) -> int:
            raise RuntimeError("unexpected boom")

    result = lambda_handler(
        sample_event,
        BoomContext(),
        config=fake_config,
    )
    assert result["statusCode"] == 500
    assert "Internal server error" in result["body"]


class StepFunctionsSpy:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def start_execution(self, *, stateMachineArn: str, name: str, input: str) -> None:
        self.calls.append({"stateMachineArn": stateMachineArn, "name": name, "input": input})


def test_lambda_handler_starts_step_functions_when_configured(sample_event, fake_config, lambda_context, monkeypatch):
    monkeypatch.setenv(
        "STEP_FUNCTIONS_STATE_MACHINE_ARN",
        "arn:aws:states:ap-northeast-1:123456789012:stateMachine:query",
    )
    sfn_spy = StepFunctionsSpy()

    result = lambda_handler(
        sample_event,
        lambda_context,
        config=fake_config,
        step_functions_client=sfn_spy,
    )

    assert result["statusCode"] == 202
    assert result["body"] == "Started Step Functions execution."
    assert len(sfn_spy.calls) == 1
    payload = json.loads(sfn_spy.calls[0]["input"])
    assert payload["queryId"] == "a1b2c3d4-5678-90ab-cdef-ab0"
    assert payload["detail"]["eventName"] == "StartQuery"


def test_lambda_handler_processes_step_functions_callback(
    sample_event, fake_dynamo_resource, opener_spy, fake_config, lambda_context
):
    callback_event = {
        "detail": sample_event,
        "queryResult": {
            "status": "Complete",
            "statistics": {
                "bytesScanned": 1073741824,
                "recordsMatched": 1,
                "recordsScanned": 10,
            },
        },
    }
    result = lambda_handler(
        callback_event,
        lambda_context,
        config=fake_config,
        dynamodb_resource=fake_dynamo_resource,
        opener=opener_spy,
    )
    assert result["statusCode"] == 200
    assert fake_dynamo_resource.table.last_put_item is not None
    assert opener_spy.calls == 1


def test_send_slack_notification_anomaly_changes_color_and_title(fake_config, opener_spy):
    record = {
        "query_id": "q-999",
        "user_name": "alice",
        "scanned_bytes": 2 * 1073741824,
        "scanned_gb": 2.0,
        "estimated_cost": 0.01,
        "records_matched": 1,
        "records_scanned": 10,
        "log_groups": ["/aws/lambda/app"],
        "is_warning": True,
        "is_anomaly": True,
    }
    send_slack_notification(fake_config, record, opener=opener_spy)
    assert opener_spy.calls == 1
    payload = json.loads(opener_spy.last_request["kwargs"]["body"].decode("utf-8"))
    assert "cost anomaly" in payload["attachments"][0]["title"].lower()
    assert payload["attachments"][0]["color"] == "danger"


def test_send_slack_notification_normal_warning_uses_warning_color(fake_config, opener_spy):
    record = {
        "query_id": "q-888",
        "user_name": "alice",
        "scanned_bytes": 2 * 1073741824,
        "scanned_gb": 2.0,
        "estimated_cost": 0.01,
        "records_matched": 1,
        "records_scanned": 10,
        "log_groups": ["/aws/lambda/app"],
        "is_warning": True,
        "is_anomaly": False,
    }
    send_slack_notification(fake_config, record, opener=opener_spy)
    assert opener_spy.calls == 1
    payload = json.loads(opener_spy.last_request["kwargs"]["body"].decode("utf-8"))
    assert "query warning" in payload["attachments"][0]["title"].lower()
    assert payload["attachments"][0]["color"] == "warning"
