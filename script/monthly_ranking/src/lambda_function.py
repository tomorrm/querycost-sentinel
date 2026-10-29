"""Generate monthly CloudWatch Logs usage ranking from DynamoDB data."""

from __future__ import annotations

import json
import logging
import math
import os
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast
from urllib.parse import urlparse

import boto3
import urllib3
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


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return default


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError):
        return default


def _safe_str(value: object) -> str:
    return "" if value is None else str(value)


def escape_slack_table_cell(value: object | None) -> str:
    escaped = _safe_str(value)
    escaped = escaped.replace("&", "&amp;")
    escaped = escaped.replace("<", "&lt;")
    escaped = escaped.replace(">", "&gt;")
    escaped = escaped.replace("|", "\\|")
    escaped = escaped.replace("\r\n", "<br>")
    escaped = escaped.replace("\n", "<br>")
    return escaped


def _required_env(name: str, *, required: bool, default: str = "") -> str:
    value = os.environ.get(name, default)
    if value is None:
        value = ""
    value = str(value).strip()
    if required and value == "":
        raise InternalServerErrorException(f"{name} not configured.")
    return value


def _to_int_env(name: str, default: int, *, min_value: int = 1, max_value: int | None = None) -> int:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise InternalServerErrorException(f"Invalid integer {name}: {value}") from exc

    if parsed < min_value:
        raise InternalServerErrorException(f"{name} must >= {min_value}.")
    if max_value is not None and parsed > max_value:
        raise InternalServerErrorException(f"{name} must <= {max_value}.")
    return parsed


def _validate_slack_webhook(url: str) -> str:
    url = _safe_str(url).strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise InternalServerErrorException("SLACK_WEBHOOK_URL must use HTTPS.")
    if not parsed.netloc:
        raise InternalServerErrorException("SLACK_WEBHOOK_URL is invalid.")
    if parsed.hostname is None:
        raise InternalServerErrorException("SLACK_WEBHOOK_URL is invalid.")
    return url


@dataclass(frozen=True)
class Config:
    slack_webhook_url: str
    dynamodb_table_name: str
    aws_region: str
    aws_pagination_limit: int
    top_n: int
    dynamodb_year_month_index: str
    cost_anomaly_sigma: float
    notification_language: str


_MESSAGES: dict[str, dict[str, str]] = {
    "ja": {
        "ranking_title": "📊 *{year_month} CloudWatch Logs 利用ランキング*",
        "summary_title": "📈 サマリー",
        "top_users_title": "🏆 上位ユーザー",
        "top_n_scanned": "Top N スキャン量",
        "top_n_cost": "Top N 推定コスト",
        "top_n_queries": "Top N クエリ数",
        "ranked_users": "ランキング対象ユーザー数",
        "scan_label": "Scan",
        "cost_label": "Cost",
        "queries_label": "Queries",
        "no_data": "データがありません",
        "anomaly_title": "🚨 コスト異常（>{sigma}σ）",
        "anomaly_stats": "Mean: ${mean:.2f} | StdDev: ${stddev:.2f} | Threshold: ${threshold:.2f}",
        "error_title": "❌ エラー",
        "info_title": "ℹ️ 情報",
    },
    "en": {
        "ranking_title": "📊 *{year_month} CloudWatch Logs usage ranking*",
        "summary_title": "📈 Summary",
        "top_users_title": "🏆 Top users",
        "top_n_scanned": "Top N scanned",
        "top_n_cost": "Top N estimated cost",
        "top_n_queries": "Top N query count",
        "ranked_users": "Ranked users",
        "scan_label": "Scan",
        "cost_label": "Cost",
        "queries_label": "Queries",
        "no_data": "No data",
        "anomaly_title": "🚨 Cost anomalies (>{sigma}σ)",
        "anomaly_stats": "Mean: ${mean:.2f} | StdDev: ${stddev:.2f} | Threshold: ${threshold:.2f}",
        "error_title": "❌ Error",
        "info_title": "ℹ️ Info",
    },
}


def _message(config: Config, key: str) -> str:
    return _MESSAGES.get(config.notification_language, _MESSAGES["ja"]).get(key, key)


def load_config() -> Config:
    aws_region = os.environ.get("AWS_REGION", "ap-northeast-1")

    secret_arn = _required_env("SLACK_WEBHOOK_SECRET_ARN", required=False, default="")
    if secret_arn:
        raw_slack_url = _resolve_slack_webhook_url(secret_arn)
    else:
        raw_slack_url = _required_env("SLACK_WEBHOOK_URL", required=False, default="")

    return Config(
        slack_webhook_url=_validate_slack_webhook(raw_slack_url),
        dynamodb_table_name=_required_env("DYNAMODB_TABLE_NAME", required=True),
        aws_region=aws_region,
        aws_pagination_limit=_to_int_env("AWS_PAGINATION_LIMIT", default=100, min_value=1, max_value=1000),
        top_n=_to_int_env("TOP_N", default=10, min_value=1, max_value=100),
        dynamodb_year_month_index=_required_env("DYNAMODB_YEAR_MONTH_INDEX", required=False, default="YearMonthIndex"),
        cost_anomaly_sigma=_safe_float(os.environ.get("COST_ANOMALY_SIGMA"), 2.0),
        notification_language=os.environ.get("NOTIFICATION_LANGUAGE", "ja").strip() or "ja",
    )


def get_target_year_month() -> str:
    now = datetime.now(timezone.utc).astimezone(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def _extract_numbered_value(item: Mapping[str, object], keys: list[str], default: float) -> float:
    for key in keys:
        if key in item:
            return _safe_float(item[key], default)
    return default


def _extract_int_value(item: Mapping[str, object], keys: list[str], default: int) -> int:
    for key in keys:
        if key in item:
            return _safe_int(item[key], default)
    return default


def _extract_user(item: Mapping[str, object]) -> str:
    user = item.get("UserName")
    if isinstance(user, str) and user.strip():
        return user
    return "Unknown"


def _is_missing_gsi_error(exc: Exception, index_name: str) -> bool:
    if not isinstance(exc, ClientError):
        return False
    error = exc.response.get("Error", {}) if isinstance(exc.response, Mapping) else {}
    code = str(error.get("Code", "")).upper()
    message = str(error.get("Message", "")).lower()
    # Fallback from GSI query to scan when index is not configured/ready.
    return (
        code in {"RESOURCEINUSEEXCEPTION", "RESOURCENOTFOUND", "RESOURCEINACTIVE"}
        or (code == "VALIDATIONEXCEPTION" and "index" in message)
        or (index_name.lower() in message)
    )


def aggregate_monthly_data(
    table: Any,
    year_month: str,
    *,
    dynamodb_pagination_limit: int,
    use_gsi: bool = True,
    year_month_index: str = "YearMonthIndex",
) -> dict[str, dict[str, object]]:
    user_stats: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "total_scanned_gb": Decimal("0"),
            "total_cost": Decimal("0"),
            "query_count": 0,
            "total_records_matched": 0,
            "total_records_scanned": 0,
        }
    )

    expr_values = {":ym": year_month}
    kwargs: dict[str, object] = {
        "FilterExpression": "#ym = :ym",
        "ExpressionAttributeNames": {"#ym": "YearMonth"},
        "ExpressionAttributeValues": expr_values,
        "Limit": dynamodb_pagination_limit,
    }

    method = table.scan
    if use_gsi:
        method = table.query
        kwargs["IndexName"] = year_month_index
        kwargs["KeyConditionExpression"] = "#ym = :ym"
        kwargs.pop("FilterExpression", None)

    seen_keys: set[str] = set()

    while True:
        try:
            response = method(**kwargs)
        except ClientError as exc:
            if use_gsi and _is_missing_gsi_error(exc, str(kwargs.get("IndexName", ""))):
                logger.warning("YearMonthIndex unavailable, fallback DynamoDB scan.")
                return aggregate_monthly_data(
                    table,
                    year_month,
                    dynamodb_pagination_limit=dynamodb_pagination_limit,
                    use_gsi=False,
                    year_month_index=year_month_index,
                )
            raise InternalServerErrorException("DynamoDB query failed.") from exc

        if not isinstance(response, Mapping):
            raise InternalServerErrorException("Invalid response from DynamoDB.")

        for item in response.get("Items", []):
            if not isinstance(item, Mapping):
                continue
            item_map = dict(item)
            user = _extract_user(item_map)
            user_entry = cast("dict[str, Any]", user_stats[user])

            scanned_gb = _extract_numbered_value(item_map, ["ScannedGB", "scanned_gb"], 0.0)
            estimated_cost = _extract_numbered_value(item_map, ["EstimatedCost", "estimated_cost"], 0.0)

            scanned_gb_dec = scanned_gb if isinstance(scanned_gb, Decimal) else Decimal(str(scanned_gb))
            estimated_cost_dec = estimated_cost if isinstance(estimated_cost, Decimal) else Decimal(str(estimated_cost))

            user_entry["total_scanned_gb"] += scanned_gb_dec
            user_entry["total_cost"] += estimated_cost_dec
            user_entry["query_count"] += _extract_int_value(
                item_map,
                ["ScannedCount", "query_count", "QueryCount"],
                1,
            )
            user_entry["total_records_matched"] += _extract_int_value(
                item_map,
                ["RecordsMatched", "records_matched", "MatchedCount"],
                0,
            )
            user_entry["total_records_scanned"] += _extract_int_value(
                item_map,
                ["RecordsScanned", "records_scanned", "ScannedCount"],
                0,
            )

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        if not isinstance(last_key, Mapping):
            break

        last_key_text = json.dumps(last_key, sort_keys=True, default=str)
        if last_key_text in seen_keys:
            raise InternalServerErrorException("DynamoDB pagination stalled.")
        seen_keys.add(last_key_text)
        kwargs["ExclusiveStartKey"] = last_key

    # schema compatibility: older records may not have query count
    for stat in user_stats.values():
        if stat["query_count"] == 0:
            stat["query_count"] = 1

    return user_stats


def create_ranking(user_stats: Mapping[str, Mapping[str, object]], top_n: int) -> list[dict[str, object]]:
    ranking: list[dict[str, object]] = []
    for user, stat in user_stats.items():
        stat_any = cast("dict[str, Any]", stat)
        total_scanned_gb = stat_any["total_scanned_gb"]
        if not isinstance(total_scanned_gb, Decimal):
            total_scanned_gb = Decimal(str(total_scanned_gb))

        total_cost = stat_any["total_cost"]
        if not isinstance(total_cost, Decimal):
            total_cost = Decimal(str(total_cost))

        query_count = _safe_int(stat_any["query_count"], 0)
        avg_scanned = total_scanned_gb / query_count if query_count else Decimal("0")

        ranking.append(
            {
                "user": user,
                "total_scanned_gb": float(total_scanned_gb),
                "total_cost": float(total_cost),
                "query_count": query_count,
                "avg_scanned_gb": float(avg_scanned),
                "total_records_matched": _safe_int(stat_any["total_records_matched"], 0),
                "total_records_scanned": _safe_int(stat_any["total_records_scanned"], 0),
            }
        )

    ranking.sort(key=lambda row: float(cast(Any, row["total_scanned_gb"])), reverse=True)
    return ranking[:top_n]


def _detect_cost_anomalies(
    ranking: list[dict[str, object]], sigma: float
) -> tuple[list[dict[str, object]], float, float, float]:
    """Detect users whose cost exceeds mean + sigma * stddev within the ranked set."""
    costs = [float(cast(Any, item["total_cost"])) for item in ranking]
    if not costs or sigma <= 0:
        return [], 0.0, 0.0, 0.0

    mean = sum(costs) / len(costs)
    variance = sum((cost - mean) ** 2 for cost in costs) / max(len(costs) - 1, 1)
    stddev = math.sqrt(variance)
    threshold = mean + sigma * stddev
    anomalies = [item for item in ranking if float(cast(Any, item["total_cost"])) > threshold]
    return anomalies, mean, stddev, threshold


def send_ranking_notification(
    config: Config, year_month: str, ranking: list[dict[str, object]], *, opener: Callable[..., object] | None = None
) -> None:
    if not config.slack_webhook_url:
        return

    total_scanned_gb = sum(float(cast(Any, item["total_scanned_gb"])) for item in ranking)
    total_cost = sum(float(cast(Any, item["total_cost"])) for item in ranking)
    total_queries = sum(int(cast(Any, item["query_count"])) for item in ranking)

    top_entries: list[str] = []
    medals = ["🥇", "🥈", "🥉"]
    for idx, entry in enumerate(ranking, start=1):
        prefix = medals[idx - 1] if idx <= len(medals) else f"{idx}."
        top_entries.append(
            f"{prefix} *{escape_slack_table_cell(entry['user'])}* | "
            f"{_message(config, 'scan_label')}: {entry['total_scanned_gb']:.2f} GB | "
            f"{_message(config, 'cost_label')}: ${entry['total_cost']:.2f} | "
            f"{_message(config, 'queries_label')}: {entry['query_count']}"
        )

    anomalies, mean_cost, stddev_cost, threshold_cost = _detect_cost_anomalies(ranking, config.cost_anomaly_sigma)
    anomaly_entries: list[str] = []
    for entry in anomalies:
        anomaly_entries.append(
            f"• *{escape_slack_table_cell(entry['user'])}* | "
            f"{_message(config, 'cost_label')}: ${float(cast(Any, entry['total_cost'])):.2f} | "
            f"Threshold: ${threshold_cost:.2f}"
        )

    attachments: list[dict[str, object]] = [
        {
            "color": "#36a64f",
            "title": _message(config, "summary_title"),
            "fields": [
                {
                    "title": _message(config, "top_n_scanned"),
                    "value": f"{total_scanned_gb:.2f} GB",
                    "short": True,
                },
                {
                    "title": _message(config, "top_n_cost"),
                    "value": f"${total_cost:.2f}",
                    "short": True,
                },
                {
                    "title": _message(config, "top_n_queries"),
                    "value": f"{total_queries:,} runs",
                    "short": True,
                },
                {
                    "title": _message(config, "ranked_users"),
                    "value": f"{len(ranking)}",
                    "short": True,
                },
            ],
        },
        {
            "color": "#3AA3E3",
            "title": _message(config, "top_users_title"),
            "text": "\n".join(top_entries) if top_entries else _message(config, "no_data"),
        },
    ]

    if anomaly_entries:
        anomaly_text = _message(config, "anomaly_stats").format(
            mean=mean_cost, stddev=stddev_cost, threshold=threshold_cost
        )
        attachments.append(
            {
                "color": "danger",
                "title": _message(config, "anomaly_title").format(sigma=config.cost_anomaly_sigma),
                "text": anomaly_text + "\n" + "\n".join(anomaly_entries),
            }
        )

    message = {
        "text": _message(config, "ranking_title").format(year_month=year_month),
        "attachments": attachments,
    }

    request = opener or HTTP_CLIENT.request
    try:
        response = request(
            "POST",
            config.slack_webhook_url,
            body=json.dumps(message, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=5.0,
        )
        status = getattr(response, "status", None)
        if isinstance(status, int) and status >= 300:
            logger.warning("Failed send Slack ranking notification. status=%s", status)
    except Exception:
        logger.exception("Failed send monthly Slack notification.")


def send_error_notification(
    config: Config, data: Mapping[str, object], *, opener: Callable[..., object] | None = None
) -> None:
    if not config.slack_webhook_url:
        return

    color = "danger" if data.get("status") == "error" else "warning"
    title = _message(config, "error_title") if data.get("status") == "error" else _message(config, "info_title")

    message = {
        "attachments": [
            {
                "color": color,
                "title": title,
                "text": escape_slack_table_cell(data.get("message")),
            }
        ]
    }

    request = opener or HTTP_CLIENT.request
    try:
        request(
            "POST",
            config.slack_webhook_url,
            body=json.dumps(message, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=5.0,
        )
    except Exception:
        logger.exception("Failed send error notification.")


def lambda_handler(
    event: Mapping[str, Any] | None,
    context: Any,
    *,
    dynamodb_resource: Any | None = None,
    opener: Callable[..., object] | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    try:
        _ = event
        _ = context
        table_resource = (dynamodb_resource or boto3.resource("dynamodb", region_name=cfg.aws_region)).Table(
            cfg.dynamodb_table_name
        )
        target_year_month = get_target_year_month()

        user_stats = aggregate_monthly_data(
            table_resource,
            target_year_month,
            dynamodb_pagination_limit=cfg.aws_pagination_limit,
            use_gsi=True,
            year_month_index=cfg.dynamodb_year_month_index,
        )
        ranking = create_ranking(user_stats, cfg.top_n)

        if not ranking:
            send_error_notification(cfg, {"status": "info", "message": f"{target_year_month} no data"}, opener=opener)
            return {"statusCode": 200, "body": json.dumps({"message": "No data"})}

        send_ranking_notification(cfg, target_year_month, ranking, opener=opener)
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "ok", "yearMonth": target_year_month, "rankingCount": len(ranking)}),
        }
    except InternalServerErrorException as exc:
        send_error_notification(cfg, {"status": "error", "message": str(exc)}, opener=opener)
        return {"statusCode": 500, "body": str(exc)}
    except Exception:
        logger.exception("Monthly ranking handler failed")
        send_error_notification(cfg, {"status": "error", "message": "Internal server error"}, opener=opener)
        return {"statusCode": 500, "body": "Internal server error"}
