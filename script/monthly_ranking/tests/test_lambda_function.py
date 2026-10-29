from __future__ import annotations

import importlib.util
import json
import sys
import types
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import pytest

if "botocore" not in sys.modules:
    botocore_stub = types.ModuleType("botocore")
    botocore_exceptions_stub = types.ModuleType("botocore.exceptions")

    class ClientError(Exception):
        def __init__(self, error_response: Mapping[str, Any], operation_name: str) -> None:
            super().__init__(f"{operation_name} failed")
            self.response = dict(error_response)

    botocore_exceptions_stub.ClientError = ClientError
    botocore_stub.exceptions = botocore_exceptions_stub
    sys.modules["botocore"] = botocore_stub
    sys.modules["botocore.exceptions"] = botocore_exceptions_stub

from botocore.exceptions import ClientError

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
if "boto3" not in sys.modules:
    boto3_stub = types.ModuleType("boto3")

    def _missing_boto3_resource(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("boto3 is required at runtime; inject dynamodb_resource in tests.")

    boto3_stub.resource = _missing_boto3_resource
    sys.modules["boto3"] = boto3_stub

_spec = importlib.util.spec_from_file_location(
    "monthly_ranking_lambda_function",
    _SRC_DIR / "lambda_function.py",
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("failed to load monthly ranking lambda module")
_module = importlib.util.module_from_spec(_spec)
sys.modules["monthly_ranking_lambda_function"] = _module
_spec.loader.exec_module(_module)

Config = _module.Config
aggregate_monthly_data = _module.aggregate_monthly_data
create_ranking = _module.create_ranking
send_ranking_notification = _module.send_ranking_notification
send_error_notification = _module.send_error_notification
lambda_handler = _module.lambda_handler
load_config = _module.load_config
_detect_cost_anomalies = _module._detect_cost_anomalies
InternalServerErrorException = _module.InternalServerErrorException


@dataclass
class FakeDynamoTable:
    query_responses: list[Mapping[str, Any]]
    scan_responses: list[Mapping[str, Any]] | None = None

    def __post_init__(self) -> None:
        self.query_responses = list(self.query_responses)
        self.scan_responses = list(self.scan_responses or [])
        self.query_calls: list[dict[str, Any]] = []
        self.scan_calls: list[dict[str, Any]] = []

    def query(self, **kwargs: Any) -> Mapping[str, Any]:
        self.query_calls.append(kwargs)
        if not self.query_responses:
            return {"Items": []}
        response = self.query_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def scan(self, **kwargs: Any) -> Mapping[str, Any]:
        self.scan_calls.append(kwargs)
        if not self.scan_responses:
            return {"Items": []}
        response = self.scan_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@dataclass
class FakeDynamoResource:
    table: FakeDynamoTable

    def Table(self, _: str) -> FakeDynamoTable:
        return self.table


@dataclass
class FakeHttpResponse:
    status: int | None = 200


@dataclass
class OpenerSpy:
    calls: int = 0
    last_request: Mapping[str, Any] | None = None

    def __call__(self, method: str, url: str, **kwargs: Any) -> FakeHttpResponse:
        self.calls += 1
        self.last_request = {
            "method": method,
            "url": url,
            "kwargs": kwargs,
        }
        return FakeHttpResponse(status=200)


def _base_config(**kwargs: Any) -> Config:
    values = {
        "slack_webhook_url": "http://localhost/webhook",
        "dynamodb_table_name": "dummy-table",
        "aws_region": "ap-northeast-1",
        "aws_pagination_limit": 100,
        "top_n": 10,
        "dynamodb_year_month_index": "YearMonthIndex",
        "cost_anomaly_sigma": 2.0,
        "notification_language": "ja",
    }
    values.update(kwargs)
    return Config(**values)


def _query_notfound_error() -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "RESOURCENOTFOUND",
                "Message": "YearMonthIndex is not configured",
            }
        },
        "Query",
    )


def test_load_config_requires_dynamodb_table_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DYNAMODB_TABLE_NAME", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    with pytest.raises(InternalServerErrorException):
        load_config()


def test_aggregate_monthly_data_query_with_pagination() -> None:
    table = FakeDynamoTable(
        [
            {
                "Items": [
                    {
                        "UserName": "alice",
                        "ScannedGB": 1.2,
                        "EstimatedCost": 0.45,
                        "ScannedCount": 3,
                        "RecordsMatched": 6,
                        "RecordsScanned": 4,
                    },
                    {
                        "UserName": "alice",
                        "ScannedGB": 2,
                        "EstimatedCost": 0.55,
                        "QueryCount": 2,
                        "MatchedCount": 1,
                        "ScannedCount": 5,
                    },
                ],
                "LastEvaluatedKey": {"pk": "1"},
            },
            {
                "Items": [
                    {
                        "UserName": "bob",
                        "ScannedGB": 3,
                        "EstimatedCost": 0.1,
                        "QueryCount": 1,
                        "MatchedCount": 9,
                        "RecordsScanned": 3,
                    }
                ],
                "LastEvaluatedKey": None,
            },
        ]
    )

    stats = aggregate_monthly_data(
        table,
        "2026-07",
        dynamodb_pagination_limit=2,
        use_gsi=True,
        year_month_index="YearMonthIndex",
    )

    assert len(table.query_calls) == 2
    assert table.query_calls[0]["IndexName"] == "YearMonthIndex"
    assert table.query_calls[0]["KeyConditionExpression"] == "#ym = :ym"
    assert table.query_calls[1]["ExclusiveStartKey"] == {"pk": "1"}
    assert stats["alice"]["total_scanned_gb"] == Decimal("3.2")
    assert stats["alice"]["total_cost"] == Decimal("1.0")
    assert stats["alice"]["query_count"] == 8
    assert stats["alice"]["total_records_matched"] == 7
    assert stats["alice"]["total_records_scanned"] == 9
    assert stats["bob"]["query_count"] == 1
    assert stats["bob"]["total_scanned_gb"] == Decimal("3")


def test_aggregate_monthly_data_fallback_to_scan_when_gsi_missing() -> None:
    table = FakeDynamoTable(
        [_query_notfound_error()],
        [
            {
                "Items": [
                    {
                        "UserName": "alice",
                        "ScannedGB": 1.5,
                        "EstimatedCost": 0.3,
                        "QueryCount": 3,
                    }
                ]
            }
        ],
    )

    stats = aggregate_monthly_data(
        table,
        "2026-07",
        dynamodb_pagination_limit=5,
        use_gsi=True,
    )

    assert len(table.query_calls) == 1
    assert len(table.scan_calls) == 1
    assert stats["alice"]["query_count"] == 3


def test_aggregate_monthly_data_detects_pagination_stall() -> None:
    table = FakeDynamoTable(
        [
            {
                "Items": [{"UserName": "alice", "ScannedGB": 1}],
                "LastEvaluatedKey": {"pk": "1"},
            },
            {
                "Items": [{"UserName": "alice", "ScannedGB": 1}],
                "LastEvaluatedKey": {"pk": "1"},
            },
        ]
    )

    with pytest.raises(InternalServerErrorException):
        aggregate_monthly_data(table, "2026-07", dynamodb_pagination_limit=10)


def test_create_ranking_sorts_and_limits() -> None:
    ranking = create_ranking(
        {
            "alice": {
                "total_scanned_gb": Decimal("1.5"),
                "total_cost": Decimal("0.8"),
                "query_count": 2,
                "total_records_matched": 4,
                "total_records_scanned": 10,
            },
            "bob": {
                "total_scanned_gb": Decimal("3.1"),
                "total_cost": Decimal("0.4"),
                "query_count": 1,
                "total_records_matched": 1,
                "total_records_scanned": 5,
            },
            "charlie": {
                "total_scanned_gb": Decimal("2.0"),
                "total_cost": Decimal("0.3"),
                "query_count": 1,
                "total_records_matched": 2,
                "total_records_scanned": 6,
            },
        },
        2,
    )

    assert [entry["user"] for entry in ranking] == ["bob", "charlie"]
    assert ranking[0]["avg_scanned_gb"] == 3.1

    def _ranking_payload() -> None:
        pass


def test_send_ranking_notification_skips_when_webhook_empty() -> None:
    ranking = [{"user": "alice", "total_scanned_gb": 1.5, "total_cost": 0.8, "query_count": 2}]
    opener = OpenerSpy()
    config = _base_config(slack_webhook_url="")
    send_ranking_notification(config, "2026-07", ranking, opener=opener)
    assert opener.calls == 0


def test_send_ranking_notification_uses_english() -> None:
    ranking = [{"user": "alice", "total_scanned_gb": 1.5, "total_cost": 0.8, "query_count": 2}]
    opener = OpenerSpy()
    config = _base_config(notification_language="en")
    send_ranking_notification(config, "2026-07", ranking, opener=opener)
    assert opener.calls == 1
    payload = json.loads(opener.last_request["kwargs"]["body"].decode("utf-8"))
    assert "CloudWatch Logs usage ranking" in payload["text"]
    assert payload["attachments"][0]["title"] == "📈 Summary"


def test_lambda_handler_sends_no_data_notification() -> None:
    table = FakeDynamoTable([{"Items": []}])
    opener = OpenerSpy()
    resource = FakeDynamoResource(table)
    config = _base_config()
    fixed_month = "2026-07"
    original_get_target_year_month = _module.get_target_year_month
    _module.get_target_year_month = lambda: fixed_month

    try:
        response = lambda_handler(
            {"eventName": "Invoke"},
            object(),
            dynamodb_resource=resource,
            opener=opener,
            config=config,
        )
    finally:
        _module.get_target_year_month = original_get_target_year_month

    assert response["statusCode"] == 200
    assert response["body"] == json.dumps({"message": "No data"})
    assert opener.calls == 1
    sent = json.loads(opener.last_request["kwargs"]["body"].decode("utf-8"))
    assert sent["attachments"][0]["title"] == "ℹ️ 情報"
    assert sent["attachments"][0]["text"] == f"{fixed_month} no data"
    assert len(sent["attachments"]) == 1


def test_load_config_validates_default_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "monthly-table")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("AWS_PAGINATION_LIMIT", "20")
    monkeypatch.setenv("TOP_N", "7")
    monkeypatch.setenv("DYNAMODB_YEAR_MONTH_INDEX", "CustomIndex")

    config = load_config()
    assert config.dynamodb_table_name == "monthly-table"
    assert config.aws_pagination_limit == 20
    assert config.top_n == 7
    assert config.dynamodb_year_month_index == "CustomIndex"
    assert config.slack_webhook_url == ""


def test_load_config_rejects_invalid_slack_webhook_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "monthly-table")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "http://example.com/hooks/abc")
    with pytest.raises(InternalServerErrorException):
        load_config()


def test_load_config_rejects_empty_host_slack_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "monthly-table")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://")
    with pytest.raises(InternalServerErrorException):
        load_config()


class _FakeSecretsManagerClient:
    def __init__(self, secret_value: str) -> None:
        self._secret_value = secret_value

    def get_secret_value(self, *, SecretId: str) -> dict[str, object]:  # noqa: N803
        return {"SecretString": self._secret_value}


def test_load_config_resolves_slack_url_from_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "monthly-table")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("SLACK_WEBHOOK_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123456789012:secret:test")
    monkeypatch.setattr(_module, "_resolved_slack_url", None)

    original_client = getattr(_module.boto3, "client", None)
    _module.boto3.client = lambda service_name, **_: (
        _FakeSecretsManagerClient("https://example.com/secret") if service_name == "secretsmanager" else None
    )
    try:
        config = load_config()
    finally:
        if original_client is None:
            delattr(_module.boto3, "client")
        else:
            _module.boto3.client = original_client

    assert config.slack_webhook_url == "https://example.com/secret"


def test_send_error_notification_skips_when_webhook_empty() -> None:
    config = _base_config(slack_webhook_url="")
    send_error_notification(config, {"status": "error", "message": "boom"})
    # No exception and no network call means success.


def test_send_error_notification_sends_error_message() -> None:
    config = _base_config()
    opener = OpenerSpy()
    send_error_notification(config, {"status": "error", "message": "something went wrong"}, opener=opener)
    assert opener.calls == 1
    payload = json.loads(opener.last_request["kwargs"]["body"].decode("utf-8"))
    assert payload["attachments"][0]["title"] == "❌ エラー"
    assert "something went wrong" in payload["attachments"][0]["text"]


def test_lambda_handler_unexpected_error_sends_notification() -> None:
    class ExplodingTable:
        def query(self, **kwargs: Any) -> Mapping[str, Any]:
            raise RuntimeError("unexpected boom")

        def scan(self, **kwargs: Any) -> Mapping[str, Any]:
            raise RuntimeError("unexpected boom")

    class ExplodingResource:
        def Table(self, _: str) -> ExplodingTable:
            return ExplodingTable()

    config = _base_config()
    opener = OpenerSpy()
    result = lambda_handler(
        {"eventName": "Invoke"},
        object(),
        dynamodb_resource=ExplodingResource(),
        opener=opener,
        config=config,
    )
    assert result["statusCode"] == 500
    assert "Internal server error" in result["body"]
    assert opener.calls == 1
    payload = json.loads(opener.last_request["kwargs"]["body"].decode("utf-8"))
    assert payload["attachments"][0]["title"] == "❌ エラー"


def test_detect_cost_anomalies_empty() -> None:
    anomalies, mean, stddev, threshold = _detect_cost_anomalies([], 2.0)
    assert anomalies == []
    assert mean == 0.0
    assert stddev == 0.0
    assert threshold == 0.0


def test_detect_cost_anomalies_flags_high_spender() -> None:
    ranking = [
        {"user": "alice", "total_cost": 1.0},
        {"user": "bob", "total_cost": 1.0},
        {"user": "carol", "total_cost": 1.0},
        {"user": "dave", "total_cost": 20.0},
    ]
    anomalies, mean, stddev, threshold = _detect_cost_anomalies(ranking, 1.0)
    assert [a["user"] for a in anomalies] == ["dave"]
    assert mean > 0
    assert stddev > 0
    assert threshold == mean + 1.0 * stddev


def test_detect_cost_anomalies_disabled() -> None:
    ranking = [
        {"user": "alice", "total_cost": 1.0},
        {"user": "bob", "total_cost": 10.0},
    ]
    anomalies, _mean, _stddev, _threshold = _detect_cost_anomalies(ranking, 0.0)
    assert anomalies == []
