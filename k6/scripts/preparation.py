"""Existing-API preparation utilities. Never imported or invoked by a k6 iteration."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from common import http_url, positive_integer, read_json, result_now


class PreparationError(ValueError):
    def __init__(self, message, http_status=None, details=None):
        super().__init__(message)
        self.http_status = http_status
        self.details = details or {}


def error_details(error, method, path, request_context=None):
    reason = getattr(error, 'reason', error)
    return {'timestamp': result_now(), 'api_name': path, 'method': method,
            'request_context': request_context, 'exception_class': type(error).__name__,
            'exception_repr': repr(error), 'exception_str': str(error),
            'reason': str(reason), 'reason_repr': repr(reason), 'reason_class': type(reason).__name__,
            'errno': getattr(reason, 'errno', None), 'http_status': getattr(error, 'code', None)}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def token_pool_from_env():
    path, single = os.environ.get("TEST_TOKENS_FILE"), os.environ.get("TEST_ACCESS_TOKEN")
    if bool(path) == bool(single):
        raise ValueError("Set exactly one of TEST_TOKENS_FILE or TEST_ACCESS_TOKEN")
    entries = read_json(path) if path else [{"accessToken": single}]
    if not isinstance(entries, list) or not entries:
        raise ValueError("Token pool must be nonempty")
    tokens = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Token pool entry must be an object")
        token = entry.get("accessToken")
        if not isinstance(token, str) or not token or "<" in token or any(c.isspace() for c in token):
            raise ValueError("Token pool requires real preissued accessToken values")
        if token in tokens:
            raise ValueError("Duplicate JWT entries in token pool")
        tokens.add(token)
        if "memberId" in entry:
            positive_integer(entry["memberId"], "token pool memberId")
    return entries


def api_data(base_url, method, path, body=None, token=None, timeout=30, request_context=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    payload = None if body is None else json.dumps(body).encode("utf-8")
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = Request(http_url(base_url, "BASE_URL") + path, data=payload, headers=headers, method=method)
    try:
        # Catalog POSTs have no idempotency contract. Never retry an ambiguous outcome.
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            envelope = json.load(response)
            if (response.status != 200 or not isinstance(envelope, dict)
                    or envelope.get("success") is not True or envelope.get("errorCode") is not None
                    or envelope.get("message") is not None):
                raise PreparationError(f"{method} {path}: unexpected API envelope")
            return envelope.get("data")
    except HTTPError as error:
        context = f"; {request_context}" if request_context else ""
        raise PreparationError(
            f"{method} {path}: HTTP {error.code}; url={request.full_url}{context}; no automatic retry",
            error.code, error_details(error, method, path, request_context),
        ) from None
    except URLError as error:
        reason = error.reason
        reason_type = type(reason).__name__
        context = f"; {request_context}" if request_context else ""
        raise PreparationError(
            f"{method} {path}: type={type(error).__name__}; reason_type={reason_type}; "
            f"reason={reason!r}; str={str(error)!r}; url={request.full_url}{context}; "
            "outcome may be unknown, no automatic retry", details=error_details(error, method, path, request_context)
        ) from None
    except (TimeoutError, OSError) as error:
        context = f"; {request_context}" if request_context else ""
        raise PreparationError(
            f"{method} {path}: type={type(error).__name__}; reason_type={type(error).__name__}; "
            f"reason={error!r}; str={str(error)!r}; url={request.full_url}{context}; "
            "outcome may be unknown, no automatic retry", details=error_details(error, method, path, request_context)
        ) from None
    except (ValueError, UnicodeError) as error:
        if isinstance(error, PreparationError):
            raise
        raise PreparationError(f"{method} {path}: invalid JSON response; no automatic retry") from None


def verify_initial_seats(base_url, performance_time_id, token, conditions, timeout=30):
    seats = api_data(base_url, "GET", f"/api/seats/{performance_time_id}", token=token, timeout=timeout)
    if not isinstance(seats, list) or len(seats) != conditions["seatCount"]:
        raise PreparationError("Fixture seat count does not match its conditions")
    seen = set()
    identities = set()
    for seat in seats:
        if not isinstance(seat, dict):
            raise PreparationError("Invalid fixture seat response")
        identity = positive_integer(seat.get("id"), "fixture seat id")
        if identity in identities:
            raise PreparationError("Duplicate fixture seat id")
        identities.add(identity)
        row = positive_integer(seat.get("rowNumber"), "fixture seat row")
        number = positive_integer(seat.get("seatNumber"), "fixture seat number")
        position = (row, number)
        if (position in seen or row > conditions["rows"] or number > conditions["seatsPerRow"]
                or seat.get("section") != conditions["section"] or seat.get("grade") != conditions["grade"]
                or seat.get("price") != conditions["price"] or seat.get("status") != "AVAILABLE"):
            raise PreparationError("Fixture requires the recorded layout/price and all seats AVAILABLE")
        seen.add(position)
    return sorted(seats, key=lambda seat: seat["id"])
