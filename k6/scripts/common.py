"""Runner/report utilities; no calls from the k6 load generation path."""
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

K6_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = K6_ROOT.parent
RESULT_TIMEZONE = timezone(timedelta(hours=9), "KST")
APIS = (
    "seat-map", "selection-summary", "pre-reserve", "payment-prepare",
    "payment-verify", "reservation-detail",
)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def result_time(value):
    """KST representation of an instant; never shift the underlying epoch."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        instant = datetime.fromtimestamp(value, RESULT_TIMEZONE)
    elif isinstance(value, datetime):
        instant = value
    else:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("Result timestamps require an explicit UTC offset")
    return instant.astimezone(RESULT_TIMEZONE).isoformat(timespec="microseconds")


def result_now():
    return result_time(datetime.now(RESULT_TIMEZONE))


def result_stamp():
    # Letters keep existing fixture-claim / idempotency cleanup validation valid.
    return datetime.now(RESULT_TIMEZONE).strftime("%Y%m%dT%H%M%S.%fKST")


def write_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def http_url(value, name):
    parsed = urlsplit(value or "")
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or any(c.isspace() for c in value) or "<" in value):
        raise ValueError(f"{name} must be a real HTTP(S) base URL without credentials/query/fragment")
    # Force validation of invalid/out-of-range port numbers.
    parsed.port
    return value.rstrip("/")


def positive_integer(value, name, maximum=None):
    if not re.fullmatch(r"[1-9]\d*", str(value or "")):
        raise ValueError(f"{name} must be a positive integer")
    result = int(value)
    if result > (maximum or 2**53 - 1):
        raise ValueError(f"{name} exceeds its maximum")
    return result


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def metric_values(summary, name):
    return summary.get("metrics", {}).get(name, {}).get("values", {})


def measured(value):
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "N/A"
    return str(value) if isinstance(value, int) else f"{value:.3f}"


def point_time_range(path):
    """Actual journey window, not Python/k6 initialization or post-processing time."""
    starts, ends = [], []
    if not Path(path).exists():
        return None, None
    with Path(path).open(encoding="utf-8") as source:
        for line in source:
            event = json.loads(line)
            if event.get("type") != "Point" or event.get("data", {}).get("value") != 1:
                continue
            point = event["data"].get("time")
            if event.get("metric") == "journey_started":
                starts.append(point)
            elif event.get("metric") == "journey_finished":
                ends.append(point)
    key = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (result_time(min(starts, key=key)) if starts else None,
            result_time(max(ends, key=key)) if ends else None)
