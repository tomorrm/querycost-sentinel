import pytest
from lambda_exceptions import InternalServerErrorException
from lambda_function import (
    _normalize_log_groups,
    _parse_timestamp_to_utc,
    _resolve_query_wait_budget,
    _resolve_slack_webhook_url,
    _to_float_env,
    _to_int_env,
    _validate_slack_webhook,
    convert_time,
    escape_slack_table_cell,
    extract_query_id,
    extract_user_name,
)


def test_escape_slack_table_cell_none() -> None:
    assert escape_slack_table_cell(None) == ""


def test_escape_slack_table_cell_special_chars() -> None:
    raw = "&<>|\n"
    assert escape_slack_table_cell(raw) == "&amp;&lt;&gt;\\|<br>"


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        ({"sessionContext": {"sessionIssuer": {"userName": "issuer.user"}}}, "issuer.user"),
        ({"principalId": "EXAMPLEPRINCIPALID:example.user"}, "example.user"),
        ({"arn": "arn:aws:iam::123456789012:user/example.user"}, "example.user"),
        ({"userName": "direct.user"}, "direct.user"),
        ({}, "Unknown"),
    ],
)
def test_extract_user_name(identity, expected):
    assert extract_user_name(identity) == expected


def test_extract_query_id_from_additional_event_data() -> None:
    detail = {"additionalEventData": {"queryId": "qid-123"}}
    assert extract_query_id(detail) == "qid-123"


def test_extract_query_id_from_response_elements() -> None:
    detail = {"responseElements": {"queryId": "qid-456"}}
    assert extract_query_id(detail) == "qid-456"


def test_extract_query_id_from_request_parameters() -> None:
    detail = {"requestParameters": {"queryId": "qid-789"}}
    assert extract_query_id(detail) == "qid-789"


def test_extract_query_id_missing_returns_empty() -> None:
    assert extract_query_id({}) == ""


def test_normalize_log_groups_from_request_parameters() -> None:
    detail = {
        "requestParameters": {
            "logGroupNames": [
                "arn:aws:logs:us-east-1:123456789012:log-group:/ecs/",
                "arn:aws:logs:us-east-1:123456789012:log-group:/ecs/nginx",
            ]
        }
    }

    assert _normalize_log_groups(detail) == ["/ecs/", "/ecs/nginx"]


def test_normalize_log_groups_from_resources() -> None:
    detail = {
        "requestParameters": {},
        "resources": [
            {"ARN": "arn:aws:logs:us-east-1:123456789012:log-group:/ecs/"},
            {"ARN": "arn:aws:logs:us-east-1:123456789012:log-group:/ecs/nginx"},
            {"ARN": "arn:aws:logs:us-east-1:123456789012:log-group:/ecs/"},
        ],
    }

    assert _normalize_log_groups(detail) == ["/ecs/", "/ecs/nginx"]


def test_normalize_log_groups_without_groups() -> None:
    assert _normalize_log_groups({}) == []


def test_convert_time_with_epoch() -> None:
    got = convert_time(0)
    assert got.startswith("1970/01/01 09:00:00.")
    assert got.endswith(".000000")


def test_parse_timestamp_invalid() -> None:
    with pytest.raises(InternalServerErrorException):
        _parse_timestamp_to_utc("not-a-date")


def test_to_int_env_validates_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUERY_WAIT_SECONDS", "600")
    assert _to_int_env("QUERY_WAIT_SECONDS", 60, min_value=1, max_value=900) == 600

    monkeypatch.setenv("QUERY_WAIT_SECONDS", "0")
    with pytest.raises(InternalServerErrorException):
        _to_int_env("QUERY_WAIT_SECONDS", 60, min_value=1, max_value=900)

    monkeypatch.setenv("QUERY_WAIT_SECONDS", "901")
    with pytest.raises(InternalServerErrorException):
        _to_int_env("QUERY_WAIT_SECONDS", 60, min_value=1, max_value=900)


def test_to_float_env_rejects_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRICE_PER_GB", "-0.1")
    with pytest.raises(InternalServerErrorException):
        _to_float_env("PRICE_PER_GB", min_value=0.0)


def test_to_float_env_validates_maximum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRICE_PER_GB", "1000001")
    with pytest.raises(InternalServerErrorException):
        _to_float_env("PRICE_PER_GB", min_value=0.0, max_value=1_000_000.0)


def test_to_float_env_requires_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRICE_PER_GB", raising=False)
    with pytest.raises(InternalServerErrorException):
        _to_float_env("PRICE_PER_GB", min_value=0.0)


def test_validate_slack_webhook_rejects_http() -> None:
    with pytest.raises(InternalServerErrorException):
        _validate_slack_webhook("http://example.com/hook")


def test_validate_slack_webhook_rejects_empty_host() -> None:
    with pytest.raises(InternalServerErrorException):
        _validate_slack_webhook("https://")


def test_validate_slack_webhook_accepts_empty_string() -> None:
    assert _validate_slack_webhook("") == ""


class _FakeLambdaContext:
    def __init__(self, remaining_millis: int) -> None:
        self._remaining_millis = remaining_millis

    def get_remaining_time_in_millis(self) -> int:
        return self._remaining_millis


def test_resolve_query_wait_budget_uses_lambda_context() -> None:
    context = _FakeLambdaContext(remaining_millis=9000)
    assert _resolve_query_wait_budget(120, context) == 7

    immediate = _FakeLambdaContext(remaining_millis=1500)
    assert _resolve_query_wait_budget(120, immediate) == 1


def test_resolve_query_wait_budget_without_context() -> None:
    assert _resolve_query_wait_budget(60, None) == 60


class _FakeSecretsManagerClient:
    def __init__(self, secret_value: str) -> None:
        self._secret_value = secret_value

    def get_secret_value(self, *, SecretId: str) -> dict[str, object]:  # noqa: N803
        return {"SecretString": self._secret_value}


def test_resolve_slack_webhook_url_from_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lambda_function._resolved_slack_url", None)
    monkeypatch.setattr(
        "lambda_function.boto3.client",
        lambda service_name, **_: (
            _FakeSecretsManagerClient("https://example.com/secret") if service_name == "secretsmanager" else None
        ),
    )
    assert (
        _resolve_slack_webhook_url("arn:aws:secretsmanager:us-east-1:123456789012:secret:test")
        == "https://example.com/secret"
    )


def test_resolve_slack_webhook_url_caches_result(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class SpyClient:
        def get_secret_value(self, *, SecretId: str) -> dict[str, object]:  # noqa: N803
            calls.append(SecretId)
            return {"SecretString": "https://example.com/cached"}

    monkeypatch.setattr("lambda_function._resolved_slack_url", None)
    monkeypatch.setattr("lambda_function.boto3.client", lambda service_name, **_: SpyClient())
    assert (
        _resolve_slack_webhook_url("arn:aws:secretsmanager:us-east-1:123456789012:secret:test")
        == "https://example.com/cached"
    )
    assert (
        _resolve_slack_webhook_url("arn:aws:secretsmanager:us-east-1:123456789012:secret:test")
        == "https://example.com/cached"
    )
    assert len(calls) == 1
