"""Pure rules for uptime checks, stats, and chart series."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_TIMEOUT_SECONDS = 30
MIN_INTERVAL_SECONDS = 15
MAX_INTERVAL_SECONDS = 24 * 60 * 60
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 120

# A completed HTTP response in this range means the server answered successfully.
UP_STATUS_MIN = 200
UP_STATUS_MAX = 399


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def clamp_interval(seconds: int) -> int:
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, int(seconds)))


def clamp_timeout(seconds: int) -> int:
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, int(seconds)))


def is_up_status(http_status: int | None) -> bool:
    if http_status is None:
        return False
    return UP_STATUS_MIN <= http_status <= UP_STATUS_MAX


def timeout_error(timeout_seconds: int) -> str:
    return f"No response within {timeout_seconds}s"


def window_start(range_key: str, now: datetime | None = None) -> datetime | None:
    now = now or utcnow()
    spans = {
        "1h": timedelta(hours=1),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    span = spans.get(range_key)
    if span is None:
        return None
    return now - span


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def summarize(checks: list[dict], now: datetime | None = None) -> dict:
    """Time-weighted uptime plus latency stats for one monitor."""
    now = now or utcnow()
    if not checks:
        return {
            "uptime_percent": None,
            "checks": 0,
            "up_checks": 0,
            "down_checks": 0,
            "incidents": 0,
            "avg_ms": None,
            "p95_ms": None,
            "min_ms": None,
            "max_ms": None,
            "downtime_seconds": 0,
        }

    ordered = sorted(checks, key=lambda row: row["checked_at"])
    up_seconds = 0.0
    down_seconds = 0.0
    incidents = 0
    previous = None
    for index, row in enumerate(ordered):
        start = parse_time(row["checked_at"])
        if index + 1 < len(ordered):
            end = parse_time(ordered[index + 1]["checked_at"])
        else:
            end = now
        duration = max(0.0, (end - start).total_seconds())
        if row["status"] == "up":
            up_seconds += duration
        else:
            down_seconds += duration
            if previous != "down":
                incidents += 1
        previous = row["status"]

    total = up_seconds + down_seconds
    latencies = [
        float(row["response_ms"])
        for row in ordered
        if row["status"] == "up" and row.get("response_ms") is not None
    ]
    return {
        "uptime_percent": (up_seconds / total * 100) if total else None,
        "checks": len(ordered),
        "up_checks": sum(1 for row in ordered if row["status"] == "up"),
        "down_checks": sum(1 for row in ordered if row["status"] == "down"),
        "incidents": incidents,
        "avg_ms": (sum(latencies) / len(latencies)) if latencies else None,
        "p95_ms": percentile(latencies, 0.95),
        "min_ms": min(latencies) if latencies else None,
        "max_ms": max(latencies) if latencies else None,
        "downtime_seconds": down_seconds,
    }


def chart_series(checks: list[dict], start: datetime, end: datetime, buckets: int = 160) -> list[dict]:
    """Bucket checks so the response-time graph stays readable."""
    if end <= start:
        return []
    width = (end - start) / buckets
    ordered = sorted(checks, key=lambda row: row["checked_at"])
    points = []
    cursor = 0
    for index in range(buckets):
        bucket_start = start + width * index
        bucket_end = start + width * (index + 1)
        group = []
        while cursor < len(ordered) and parse_time(ordered[cursor]["checked_at"]) < bucket_start:
            cursor += 1
        scan = cursor
        while scan < len(ordered) and parse_time(ordered[scan]["checked_at"]) < bucket_end:
            group.append(ordered[scan])
            scan += 1
        ups = [
            row["response_ms"]
            for row in group
            if row["status"] == "up" and row.get("response_ms") is not None
        ]
        down_count = sum(1 for row in group if row["status"] == "down")
        points.append(
            {
                "t": iso(bucket_start),
                "response_ms": (sum(ups) / len(ups)) if ups else None,
                "status": "down" if down_count else ("up" if group else None),
                "down_count": down_count,
            }
        )
    return points
