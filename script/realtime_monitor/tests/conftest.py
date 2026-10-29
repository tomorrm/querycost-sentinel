import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import pytest

TEST_DIR = os.path.dirname(__file__)
PROJECT_DIR = os.path.dirname(TEST_DIR)
SRC_DIR = os.path.join(PROJECT_DIR, "src")

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from lambda_function import Config  # noqa: E402


def _load_sample_event() -> dict[str, Any]:
    sample_path = os.path.join(TEST_DIR, "sample.json")
    with open(sample_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture()
def sample_event() -> dict[str, Any]:
    return _load_sample_event()


@pytest.fixture()
def fake_config() -> Config:
    return Config(
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


@dataclass
class FakeLogsClient:
    responses: Iterable[Mapping[str, Any]]

    def __post_init__(self) -> None:
        self._responses = list(self.responses)
        self.calls = 0

    def get_query_results(self, *, queryId: str) -> Mapping[str, Any]:
        if not self._responses:
            return {"status": "Complete", "statistics": {}}
        if self.calls >= len(self._responses):
            raise AssertionError("unexpected extra get_query_results call")
        response = self._responses[self.calls]
        self.calls += 1
        return response


class FakeDynamoTable:
    def __init__(self) -> None:
        self.last_put_item: dict[str, Any] | None = None

    def put_item(self, *, Item: Mapping[str, Any]) -> Mapping[str, Any]:
        self.last_put_item = dict(Item)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


class FakeDynamoResource:
    def __init__(self) -> None:
        self.table = FakeDynamoTable()
        self.last_table_name: str | None = None

    def Table(self, name: str) -> FakeDynamoTable:
        self.last_table_name = name
        return self.table


class OpenerSpy:
    def __init__(self) -> None:
        self.calls = 0
        self.last_request: dict[str, Any] | None = None

    def __call__(self, method: str, url: str, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.last_request = {"method": method, "url": url, "args": args, "kwargs": kwargs}

        class Response:
            status = 200

        return Response()


@pytest.fixture()
def make_logs_client():
    def _factory(responses: Iterable[Mapping[str, Any]]) -> FakeLogsClient:
        return FakeLogsClient(responses)

    return _factory


@pytest.fixture()
def fake_dynamo_resource() -> FakeDynamoResource:
    return FakeDynamoResource()


@pytest.fixture()
def opener_spy() -> OpenerSpy:
    return OpenerSpy()


@pytest.fixture()
def lambda_context():
    class DummyContext:
        function_name = "test-func"
        invoked_function_arn = "arn:aws:lambda:ap-northeast-1:123456789012:function:test-function"
        aws_request_id = "00000000-0000-0000-0000-000000000000"

        def get_remaining_time_in_millis(self) -> int:
            return 30_000

    return DummyContext()
