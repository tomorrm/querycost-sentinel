"""AWS Lambda handler for CloudWatch Logs StartQuery event monitoring."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional, cast
from urllib.parse import urlparse

import boto3
import urllib3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from lambda_exceptions import InternalServerErrorException
from urllib3.util.retry import Retry


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": self.formatTime(record),
                "level": record.levelname,
                "name": record.name,
                "message": record.getMessage(),
                "pathname": record.pathname,
                "lineno": record.lineno,
            },
            ensure_ascii=False,
            default=str,
        )


def _configure_logging() -> None:
    root = logging.getLogger()
    if os.environ.get("LOG_FORMAT", "").lower() != "json":
        if not root.handlers:
            logging.basicConfig(level=logging.INFO)
        return

    formatter: logging.Formatter = _JsonFormatter()
    if root.handlers:
        for handler in root.handlers:
            handler.setFormatter(formatter)
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        root.addHandler(handler)
        root.setLevel(logging.INFO)


_configure_logging()

logger = logging.getLogger(__name__)

HTTP_CLIENT = urllib3.PoolManager(
    retries=Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504]),
)
JST_OFFSET = timezone(timedelta(hours=9))
MAX_QUERY_STRING_LENGTH = 500

_QUERY_WAIT_SECONDS_MIN = 1
_QUERY_WAIT_SECONDS_MAX = 900
_QUERY_WAIT_INTERVAL_SECONDS_MIN = 1
_QUERY_WAIT_INTERVAL_SECONDS_MAX = 60
_RECOVERY_PERIOD_DAYS_MIN = 1
_RECOVERY_PERIOD_DAYS_MAX = 365
_QUERY_PRICE_PER_GB_MAX = 1_000_000.0
_WARNING_THRESHOLD_GB_MAX = 10_000_000.0
_LAMBDA_TIMEOUT_BUFFER_SECONDS = 2

_resolved_slack_url: str | None = None


def _resolve_slack_webhook_url(secret_arn: str) -> str:
    """Fetch Slack webhook URL from Secrets Manager, with invocation-level caching."""
    global _resolved_slack_url
    if _resolved_slack_url is not None:
        return _resolved_slack_url

    try:
        client = boto3.client(
            "secretsmanager",
            region_name=os.environ.get("AWS_REGION", "ap-northeast-1"),
        )
        response = client.get_secret_value(SecretId=secret_arn)
    except ClientError as exc:
        raise InternalServerErrorException("Failed to retrieve Slack webhook secret.") from exc

    secret = response.get("SecretString")
    if not isinstance(secret, str):
        raise InternalServerErrorException("Slack webhook secret value is not a string.")

    _resolved_slack_url = secret.strip()
    return _resolved_slack_url


def _required_env(name: str, *, required: bool, default: str = "") -> str:
    value = os.environ.get(name, default)
    if value is None:
        value = ""
    value = str(value).strip()
    if required and value == "":
        raise InternalServerErrorException(f"{name} not configured.")
    return value


def _to_float_env(
    name: str,
    default: float | None = None,
    *,
    min_value: float = 0.0,
    max_value: float | None = None,
) -> float:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        if default is None:
            raise InternalServerErrorException(f"{name} is required.")
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise InternalServerErrorException(f"Invalid float {name}: {value}") from exc
    if parsed < min_value:
        raise InternalServerErrorException(f"{name} must be >= {min_value}.")
    if max_value is not None and parsed > max_value:
        raise InternalServerErrorException(f"{name} must be <= {max_value}.")
    return parsed


def _to_int_env(
    name: str,
    default: int,
    *,
    min_value: int = 0,
    max_value: int | None = None,
) -> int:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise InternalServerErrorException(f"Invalid integer {name}: {value}") from exc
    if parsed < min_value:
        raise InternalServerErrorException(f"{name} must be >= {min_value}.")
    if max_value is not None and parsed > max_value:
        raise InternalServerErrorException(f"{name} must be <= {max_value}.")
    return parsed


def _safe_str(value: Any) -> str:
    return "" if value is None else str(value)


def _safe_non_negative_int(value: Any, *, default: int = 0) -> int:
    try:
        number = int(value)
        return max(0, number)
    except (TypeError, ValueError):
        return default


def escape_slack_table_cell(value: Any) -> str:
    escaped = _safe_str(value)
    escaped = escaped.replace("&", "&amp;")
    escaped = escaped.replace("<", "&lt;")
    escaped = escaped.replace(">", "&gt;")
    escaped = escaped.replace("|", "\\|")
    escaped = escaped.replace("\r\n", "<br>")
    escaped = escaped.replace("\n", "<br>")
    return escaped


def convert_time(value: Any) -> str:
    return _parse_timestamp_to_utc(value).astimezone(JST_OFFSET).strftime("%Y/%m/%d %H:%M:%S.%f")


def _parse_timestamp_to_utc(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)

    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)

        if isinstance(value, datetime):
            return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)

        if isinstance(value, str):
            text = value.strip()
            if not text:
                return datetime.now(timezone.utc)
            if text.replace(".", "", 1).replace("-", "", 1).isdigit():
                try:
                    as_int = int(float(text))
                    return datetime.fromtimestamp(as_int, tz=timezone.utc)
                except (ValueError, OSError, OverflowError) as exc:
                    raise InternalServerErrorException("eventTime invalid.") from exc

            # Handle RFC3339-style timezone suffix.
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            return datetime.fromisoformat(text).astimezone(timezone.utc)

        raise TypeError(f"unsupported eventTime type: {type(value).__name__}")
    except (TypeError, ValueError, OSError, OverflowError) as exc:
        logger.warning("Failed parse event timestamp: %s", value)
        raise InternalServerErrorException("eventTime invalid.") from exc


@dataclass(frozen=True)
class Config:
    slack_webhook_url: str
    dynamodb_table_name: str
    price_per_gb: float
    warning_threshold_gb: float
    query_wait_seconds: int
    query_wait_interval_seconds: int
    recovery_period_days: int
    aws_region: str
    cost_anomaly_sigma: float
    cost_anomaly_lookback_hours: int


def _validate_slack_webhook(url: str) -> str:
    if url == "":
        return ""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise InternalServerErrorException("SLACK_WEBHOOK_URL must use HTTPS.")
    if not parsed.netloc:
        raise InternalServerErrorException("SLACK_WEBHOOK_URL invalid.")
    return url


def load_config() -> Config:
    secret_arn = _required_env("SLACK_WEBHOOK_SECRET_ARN", required=False, default="")
    if secret_arn:
        raw_slack_url = _resolve_slack_webhook_url(secret_arn)
    else:
        raw_slack_url = _required_env("SLACK_WEBHOOK_URL", required=False, default="")

    slack_webhook_url = _validate_slack_webhook(raw_slack_url)
    cfg = Config(
        slack_webhook_url=slack_webhook_url,
        dynamodb_table_name=_required_env("DYNAMODB_TABLE_NAME", required=True),
        price_per_gb=_to_float_env("PRICE_PER_GB", min_value=0.0, max_value=_QUERY_PRICE_PER_GB_MAX),
        warning_threshold_gb=_to_float_env(
            "WARNING_THRESHOLD_GB", 10, min_value=0.0, max_value=_WARNING_THRESHOLD_GB_MAX
        ),
        query_wait_seconds=_to_int_env(
            "QUERY_WAIT_SECONDS", 60, min_value=_QUERY_WAIT_SECONDS_MIN, max_value=_QUERY_WAIT_SECONDS_MAX
        ),
        query_wait_interval_seconds=_to_int_env(
            "QUERY_WAIT_INTERVAL_SECONDS",
            1,
            min_value=_QUERY_WAIT_INTERVAL_SECONDS_MIN,
            max_value=_QUERY_WAIT_INTERVAL_SECONDS_MAX,
        ),
        recovery_period_days=_to_int_env(
            "RECOVERY_PERIOD_DAYS", 35, min_value=_RECOVERY_PERIOD_DAYS_MIN, max_value=_RECOVERY_PERIOD_DAYS_MAX
        ),
        aws_region=os.environ.get("AWS_REGION", "ap-northeast-1"),
        cost_anomaly_sigma=_to_float_env("COST_ANOMALY_SIGMA", default=0.0, min_value=0.0, max_value=10.0),
        cost_anomaly_lookback_hours=_to_int_env("COST_ANOMALY_LOOKBACK_HOURS", default=168, min_value=1, max_value=720),
    )
    return _ensure_config_bounds(cfg)


def _normalize_event(event: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(event, Mapping):
        raise InternalServerErrorException("Invalid event payload.")
    detail = event.get("detail")
    if isinstance(detail, Mapping):
        return detail
    return event


def extract_query_id(detail: Mapping[str, Any]) -> str:
    for source in ("additionalEventData", "responseElements", "requestParameters"):
        container = detail.get(source)
        if isinstance(container, Mapping):
            query_id = container.get("queryId")
            if query_id:
                return str(query_id)
    return ""


def extract_user_name(user_identity: Mapping[str, Any] | None) -> str:
    if not isinstance(user_identity, Mapping):
        return "Unknown"

    # sessionContext.sessionIssuer.userName
    session_context = user_identity.get("sessionContext")
    if isinstance(session_context, Mapping):
        session_issuer = session_context.get("sessionIssuer")
        if isinstance(session_issuer, Mapping):
            user_name = session_issuer.get("userName")
            if isinstance(user_name, str) and user_name:
                return user_name

    # principalId: "AROAXXX:name"
    principal = user_identity.get("principalId")
    if isinstance(principal, str):
        principal_text = principal.strip()
        if ":" in principal_text:
            return principal_text.split(":", 1)[-1]
        if "/" in principal_text:
            return principal_text.split("/", 1)[-1]

    # ARN format: arn:...:user/name
    arn = user_identity.get("arn")
    if isinstance(arn, str):
        arn_text = arn.strip()
        if "/log-group/" in arn_text:
            _, tail = arn_text.rsplit("/", 1)
            return tail
        if ":" in arn_text:
            return arn_text.rsplit("/", 1)[-1]

    user_name = user_identity.get("userName")
    if isinstance(user_name, str) and user_name:
        return user_name

    return "Unknown"


def _extract_log_group_name(raw_value: Any) -> Optional[str]:
    value = _safe_str(raw_value).strip()
    if not value:
        return None
    if "/log-group/" in value:
        _, tail = value.split("/log-group/", 1)
        return tail
    if value.startswith("arn:") and ":log-group:" in value:
        _, tail = value.split(":log-group:", 1)
        return tail
    if value.startswith("/"):
        return value
    return None


def _normalize_log_groups(detail: Mapping[str, Any]) -> list[str]:
    request_parameters = detail.get("requestParameters")
    log_groups: list[str] = []
    seen: set[str] = set()

    if isinstance(request_parameters, Mapping):
        request_log_groups = request_parameters.get("logGroupNames")
        if isinstance(request_log_groups, list):
            names = [_extract_log_group_name(value) for value in request_log_groups]
            for name in names:
                if not name or name in seen:
                    continue
                seen.add(name)
                log_groups.append(name)

    if not log_groups:
        resources = detail.get("resources")
        if isinstance(resources, list):
            for resource in resources:
                if not isinstance(resource, Mapping):
                    continue
                arn = _safe_str(resource.get("ARN"))
                if not arn:
                    continue
                log_group = _extract_log_group_name(arn)
                if not log_group or log_group in seen:
                    continue
                seen.add(log_group)
                log_groups.append(log_group)

    return log_groups


def _resolve_query_wait_budget(max_wait_seconds: int, context: Any | None) -> int:
    if context is None:
        return max(1, max_wait_seconds)

    try:
        remaining_millis = int(context.get_remaining_time_in_millis())
    except (AttributeError, TypeError, ValueError):
        return max(1, max_wait_seconds)

    if remaining_millis <= 0:
        return 0

    remaining_seconds = remaining_millis // 1000 - _LAMBDA_TIMEOUT_BUFFER_SECONDS
    if remaining_seconds <= 0:
        return 1
    if max_wait_seconds <= 0:
        return max(1, remaining_seconds)

    return max(1, min(max_wait_seconds, remaining_seconds))


def wait_for_query_completion(
    logs_client: Any,
    query_id: str,
    *,
    max_wait_seconds: int,
    interval_seconds: int,
    context: Any | None = None,
) -> Mapping[str, Any]:
    if not query_id:
        raise InternalServerErrorException("queryId required.")

    resolved_max_wait = _resolve_query_wait_budget(max_wait_seconds, context)
    if resolved_max_wait <= 0:
        raise InternalServerErrorException("Query wait timeout.")

    deadline = time.monotonic() + resolved_max_wait
    interval = max(
        _QUERY_WAIT_INTERVAL_SECONDS_MIN,
        min(int(interval_seconds), _QUERY_WAIT_INTERVAL_SECONDS_MAX),
    )
    if interval <= 0:
        interval = _QUERY_WAIT_INTERVAL_SECONDS_MIN

    while True:
        try:
            response = logs_client.get_query_results(queryId=query_id)
        except Exception as exc:
            raise InternalServerErrorException("Failed call CloudWatch Logs API.") from exc

        if not isinstance(response, Mapping):
            raise InternalServerErrorException("Invalid query result payload.")

        status = _safe_str(response.get("status")).lower()
        if status == "complete":
            return response
        if status in {"failed", "cancelled", "timeout"}:
            message = _safe_str(response.get("statusDetails") or response.get("errorMessage") or "query failed")
            raise InternalServerErrorException(f"Query failed: {message}")
        if status not in {"running", "scheduled", "cancelling", "queued"}:
            raise InternalServerErrorException(f"Unexpected query status: {status}")

        if time.monotonic() >= deadline:
            raise InternalServerErrorException("Query did not complete within time limit.")
        time.sleep(interval)


def build_record(
    detail: Mapping[str, Any],
    query_result: Mapping[str, Any],
    query_id: str,
    config: Config,
) -> Mapping[str, Any]:
    statistics = query_result.get("statistics")
    if not isinstance(statistics, Mapping):
        statistics = {}

    scanned_bytes = _safe_non_negative_int(statistics.get("bytesScanned"), default=0)
    scanned_gb_float = scanned_bytes / (1024**3)
    scanned_gb = Decimal(str(scanned_gb_float))
    estimated_cost = (scanned_gb * Decimal(str(config.price_per_gb))).quantize(Decimal("0.0000000001"))

    parsed_event_time = _parse_timestamp_to_utc(detail.get("eventTime"))

    request_parameters = detail.get("requestParameters")
    if isinstance(request_parameters, Mapping):
        query_string = _safe_str(request_parameters.get("queryString"))[:MAX_QUERY_STRING_LENGTH]
    else:
        query_string = ""

    return {
        "query_id": query_id,
        "user_name": extract_user_name(
            detail.get("userIdentity") if isinstance(detail.get("userIdentity"), Mapping) else None
        ),
        "timestamp": convert_time(parsed_event_time),
        "year_month": parsed_event_time.strftime("%Y-%m"),
        "scanned_bytes": scanned_bytes,
        "scanned_gb": scanned_gb_float,
        "estimated_cost": float(f"{estimated_cost:.10f}"),
        "log_groups": _normalize_log_groups(detail),
        "query_string": query_string,
        "records_matched": int(_safe_non_negative_int(statistics.get("recordsMatched", 0))),
        "records_scanned": int(_safe_non_negative_int(statistics.get("recordsScanned", 0))),
        "is_warning": scanned_gb_float >= config.warning_threshold_gb,
    }


def save_to_dynamodb(
    config: Config,
    record: Mapping[str, Any],
    dynamodb_resource: Any | None = None,
) -> None:
    resource = dynamodb_resource or boto3.resource("dynamodb", region_name=config.aws_region)
    table = resource.Table(config.dynamodb_table_name)

    now = datetime.now(timezone.utc)
    ttl = int(now.timestamp()) + config.recovery_period_days * 24 * 3600

    item = {
        "QueryId": record.get("query_id", ""),
        "TimeStamp": record.get("timestamp", ""),
        "YearMonth": record.get("year_month", ""),
        "UserName": record.get("user_name", "Unknown"),
        "ScannedBytes": Decimal(str(record.get("scanned_bytes", 0))),
        "ScannedGB": Decimal(str(record.get("scanned_gb", 0))),
        "EstimatedCost": Decimal(str(record.get("estimated_cost", 0))),
        "LogGroups": list(record.get("log_groups", [])),
        "QueryString": record.get("query_string", ""),
        "RecordsMatched": int(record.get("records_matched", 0)),
        "RecordsScanned": int(record.get("records_scanned", 0)),
        "TTL": ttl,
        "IsWarning": bool(record.get("is_warning", False)),
    }

    try:
        table.put_item(Item=item)
    except ClientError as exc:
        raise InternalServerErrorException("Failed DynamoDB.") from exc


def _detect_cost_anomaly_for_user(
    config: Config,
    user_name: str,
    current_cost: Decimal,
    *,
    dynamodb_resource: Any | None = None,
) -> bool:
    """Compare the current query cost against the user's recent history using sample stddev."""
    if config.cost_anomaly_sigma <= 0 or not user_name:
        return False

    resource = dynamodb_resource or boto3.resource("dynamodb", region_name=config.aws_region)
    table = resource.Table(config.dynamodb_table_name)

    since = (datetime.now(timezone.utc) - timedelta(hours=config.cost_anomaly_lookback_hours)).isoformat()
    try:
        response = table.query(
            IndexName="UserNameIndex",
            KeyConditionExpression=Key("UserName").eq(user_name),
            ProjectionExpression="EstimatedCost,#ts",
            ExpressionAttributeNames={"#ts": "TimeStamp"},
        )
    except ClientError as exc:
        logger.warning("Failed to query user history for anomaly detection: %s", exc)
        return False

    items: list[dict[str, Any]] = []
    while True:
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        try:
            response = table.query(
                IndexName="UserNameIndex",
                KeyConditionExpression=Key("UserName").eq(user_name),
                ProjectionExpression="EstimatedCost,#ts",
                ExpressionAttributeNames={"#ts": "TimeStamp"},
                ExclusiveStartKey=last_key,
            )
        except ClientError as exc:
            logger.warning("Failed to paginate user history for anomaly detection: %s", exc)
            break

    costs: list[Decimal] = []
    for item in items:
        if item.get("TimeStamp", "") < since:
            continue
        value = item.get("EstimatedCost")
        if value is None:
            continue
        costs.append(Decimal(str(value)))

    if len(costs) < 2:
        return False

    mean: Decimal = sum(costs) / Decimal(len(costs))
    variance: Decimal = sum((cost - mean) ** 2 for cost in costs) / Decimal(max(len(costs) - 1, 1))
    stddev: Decimal = variance.sqrt()
    threshold: Decimal = mean + Decimal(str(config.cost_anomaly_sigma)) * stddev
    return current_cost > threshold


def send_slack_notification(
    config: Config,
    record: Mapping[str, Any],
    *,
    opener: Optional[Callable[..., Any]] = None,
) -> None:
    if not config.slack_webhook_url:
        return

    is_warning = bool(record.get("is_warning"))
    is_anomaly = bool(record.get("is_anomaly"))
    if not is_warning:
        return

    fields = [
        {"title": "Query ID", "value": escape_slack_table_cell(record.get("query_id"))},
        {"title": "User", "value": escape_slack_table_cell(record.get("user_name"))},
        {
            "title": "Scanned bytes",
            "value": f"{record.get('scanned_bytes', 0):,} bytes",
        },
        {
            "title": "Scanned GB",
            "value": f"{float(record.get('scanned_gb', 0)):.4f} GB",
        },
        {
            "title": "Estimated cost",
            "value": f"${float(record.get('estimated_cost', 0)):.6f}",
        },
        {
            "title": "Records",
            "value": f"{record.get('records_matched', 0)} / {record.get('records_scanned', 0)} matched",
        },
        {
            "title": "Log groups",
            "value": escape_slack_table_cell("<br>".join(record.get("log_groups", []))),
        },
    ]

    if is_anomaly:
        title = "🚨 CloudWatch Logs query cost anomaly"
        color = "danger"
        text = (
            f"⚠️ Scanned bytes exceeded threshold: {config.warning_threshold_gb} GB "
            f"({record['scanned_gb']:.4f} GB). This query's estimated cost is anomalous for this user."
        )
    else:
        title = "⚠️ CloudWatch Logs query warning"
        color = "warning"
        text = f"⚠️ Scanned bytes exceeded threshold: {config.warning_threshold_gb} GB ({record['scanned_gb']:.4f} GB)."

    slack_message: dict[str, Any] = {
        "text": text,
        "attachments": [
            {
                "color": color,
                "title": title,
                "text": None,
                "fields": fields,
                "footer": "QueryCost Sentinel",
                "ts": int(time.time()),
            }
        ],
    }

    request = opener or HTTP_CLIENT.request
    try:
        response = request(
            "POST",
            config.slack_webhook_url,
            body=json.dumps(slack_message, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=5.0,
        )
        status = getattr(response, "status", None)
        if isinstance(status, int) and status >= 300:
            logger.warning("Failed send Slack notification. status=%s", status)
    except Exception:
        logger.exception("Failed send Slack notification.")


def _ensure_config_bounds(cfg: Config) -> Config:
    if cfg.query_wait_seconds < _QUERY_WAIT_SECONDS_MIN:
        raise InternalServerErrorException(f"QUERY_WAIT_SECONDS must be >= {_QUERY_WAIT_SECONDS_MIN}.")
    if cfg.query_wait_seconds > _QUERY_WAIT_SECONDS_MAX:
        raise InternalServerErrorException(f"QUERY_WAIT_SECONDS must be <= {_QUERY_WAIT_SECONDS_MAX}.")
    if cfg.query_wait_interval_seconds < _QUERY_WAIT_INTERVAL_SECONDS_MIN:
        raise InternalServerErrorException(
            f"QUERY_WAIT_INTERVAL_SECONDS must be >= {_QUERY_WAIT_INTERVAL_SECONDS_MIN}."
        )
    if cfg.query_wait_interval_seconds > _QUERY_WAIT_INTERVAL_SECONDS_MAX:
        raise InternalServerErrorException(
            f"QUERY_WAIT_INTERVAL_SECONDS must be <= {_QUERY_WAIT_INTERVAL_SECONDS_MAX}."
        )
    if cfg.recovery_period_days < _RECOVERY_PERIOD_DAYS_MIN:
        raise InternalServerErrorException(f"RECOVERY_PERIOD_DAYS must be >= {_RECOVERY_PERIOD_DAYS_MIN}.")
    if cfg.recovery_period_days > _RECOVERY_PERIOD_DAYS_MAX:
        raise InternalServerErrorException(f"RECOVERY_PERIOD_DAYS must be <= {_RECOVERY_PERIOD_DAYS_MAX}.")
    if cfg.price_per_gb < 0:
        raise InternalServerErrorException("PRICE_PER_GB must be >= 0.")
    if cfg.price_per_gb > _QUERY_PRICE_PER_GB_MAX:
        raise InternalServerErrorException("PRICE_PER_GB must be reasonable value.")
    if cfg.warning_threshold_gb < 0:
        raise InternalServerErrorException("WARNING_THRESHOLD_GB must be >= 0.")
    if cfg.warning_threshold_gb > _WARNING_THRESHOLD_GB_MAX:
        raise InternalServerErrorException("WARNING_THRESHOLD_GB must be reasonable value.")
    if cfg.query_wait_interval_seconds > cfg.query_wait_seconds:
        raise InternalServerErrorException("QUERY_WAIT_INTERVAL_SECONDS must not exceed QUERY_WAIT_SECONDS.")
    return cfg


def _start_query_state_machine(
    state_machine_arn: str,
    query_id: str,
    detail: Mapping[str, Any],
    *,
    region: str,
    step_functions_client: Any | None = None,
) -> None:
    client = step_functions_client or boto3.client("stepfunctions", region_name=region)
    name = f"query-{query_id[:80]}"
    client.start_execution(
        stateMachineArn=state_machine_arn,
        name=name,
        input=json.dumps({"queryId": query_id, "detail": dict(detail)}, default=str),
    )


def lambda_handler(
    event: Mapping[str, Any] | None,
    context: Any,
    *,
    logs_client: Any | None = None,
    dynamodb_resource: Any | None = None,
    opener: Optional[Callable[..., Any]] = None,
    config: Config | None = None,
    step_functions_client: Any | None = None,
) -> dict[str, Any]:
    try:
        cfg = config or load_config()

        # Step Functions callback payload contains the query result directly.
        if isinstance(event, Mapping) and isinstance(event.get("queryResult"), Mapping):
            detail = _normalize_event(event)
            query_id = extract_query_id(detail)
            query_result = event["queryResult"]
            record = cast("dict[str, Any]", build_record(detail, query_result, query_id, cfg))
            user_name = str(record.get("user_name", "Unknown"))
            record["is_anomaly"] = _detect_cost_anomaly_for_user(
                cfg,
                user_name,
                Decimal(str(record.get("estimated_cost", 0))),
                dynamodb_resource=dynamodb_resource,
            )
            save_to_dynamodb(cfg, record, dynamodb_resource=dynamodb_resource)
            send_slack_notification(cfg, record, opener=opener)
            return {"statusCode": 200, "body": "ok"}

        detail = _normalize_event(event)
        event_name = detail.get("eventName")
        if not isinstance(event_name, str):
            raise InternalServerErrorException("eventName required.")
        if event_name != "StartQuery":
            return {"statusCode": 200, "body": "Not StartQuery event."}

        query_id = extract_query_id(detail)
        if not query_id:
            raise InternalServerErrorException("queryId required.")

        state_machine_arn = os.environ.get("STEP_FUNCTIONS_STATE_MACHINE_ARN", "")
        if state_machine_arn:
            _start_query_state_machine(
                state_machine_arn,
                query_id,
                detail,
                region=cfg.aws_region,
                step_functions_client=step_functions_client,
            )
            return {"statusCode": 202, "body": "Started Step Functions execution."}

        logs_client = logs_client or boto3.client("logs", region_name=cfg.aws_region)
        query_result = wait_for_query_completion(
            logs_client,
            query_id,
            max_wait_seconds=cfg.query_wait_seconds,
            interval_seconds=cfg.query_wait_interval_seconds,
            context=context,
        )

        record = cast("dict[str, Any]", build_record(detail, query_result, query_id, cfg))
        user_name = str(record.get("user_name", "Unknown"))
        record["is_anomaly"] = _detect_cost_anomaly_for_user(
            cfg,
            user_name,
            Decimal(str(record.get("estimated_cost", 0))),
            dynamodb_resource=dynamodb_resource,
        )
        save_to_dynamodb(cfg, record, dynamodb_resource=dynamodb_resource)
        send_slack_notification(cfg, record, opener=opener)
        return {"statusCode": 200, "body": "ok"}
    except InternalServerErrorException as exc:
        logger.warning("Handler rejected event: %s", exc)
        return {"statusCode": 500, "body": str(exc)}
    except Exception:
        logger.exception("Unhandled error in realtime monitor handler")
        return {"statusCode": 500, "body": "Internal server error"}
