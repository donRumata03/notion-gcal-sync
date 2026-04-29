from __future__ import annotations

from googleapiclient.errors import HttpError

from app.gcal_client import _execute_with_retries, _is_retryable_http_error


class _Response(dict):
    def __init__(self, status: int) -> None:
        super().__init__()
        self.status = status
        self.reason = "reason"

    def getheaders(self) -> dict:
        return {}


def _http_error(status: int, reason: str = "rateLimitExceeded") -> HttpError:
    content = f'{{"error": {{"errors": [{{"reason": "{reason}"}}]}}}}'.encode()
    return HttpError(_Response(status), content)


def test_rate_limit_http_error_is_retryable() -> None:
    assert _is_retryable_http_error(_http_error(403, "rateLimitExceeded")) is True
    assert _is_retryable_http_error(_http_error(403, "userRateLimitExceeded")) is True
    assert _is_retryable_http_error(_http_error(429, "quotaExceeded")) is True
    assert _is_retryable_http_error(_http_error(400, "badRequest")) is False


def test_execute_with_retries_retries_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr("app.gcal_client.time.sleep", lambda seconds: None)
    calls = {"count": 0}

    def operation() -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise _http_error(403)
        return "ok"

    assert _execute_with_retries(operation, max_attempts=3) == "ok"
    assert calls["count"] == 3
