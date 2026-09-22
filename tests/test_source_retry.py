"""Search adapters retry rate limits and transient failures."""
import io
import json
import urllib.error

import pytest

from slr_engine.sources.openalex import OpenAlexAdapter


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("https://api.example/x", code, "error", headers, None)


class _Flaky:
    """Callable that raises the given errors in turn, then returns 'ok'."""

    def __init__(self, *errors):
        self.errors = list(errors)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


@pytest.fixture
def adapter():
    a = OpenAlexAdapter()
    a.waits = []
    a._polite_sleep = a.waits.append
    return a


def test_rate_limit_and_server_errors_are_retried_with_backoff(adapter):
    fetch = _Flaky(_http_error(429), _http_error(503), urllib.error.URLError("reset"))
    assert adapter._with_retry(fetch) == "ok"
    assert fetch.calls == 4
    assert adapter.waits == [2.0, 4.0, 8.0]


def test_retry_after_header_is_honoured(adapter):
    fetch = _Flaky(_http_error(429, retry_after="7"))
    assert adapter._with_retry(fetch) == "ok"
    assert adapter.waits == [7.0]


def test_client_errors_fail_at_once(adapter):
    fetch = _Flaky(_http_error(404))
    with pytest.raises(urllib.error.HTTPError):
        adapter._with_retry(fetch)
    assert fetch.calls == 1
    assert adapter.waits == []


def test_exhausted_quota_is_not_waited_out(adapter):
    fetch = _Flaky(_http_error(429, retry_after="86400"))
    with pytest.raises(urllib.error.HTTPError):
        adapter._with_retry(fetch)
    assert adapter.waits == []


def test_gives_up_after_max_retries(adapter):
    fetch = _Flaky(*[_http_error(429) for _ in range(10)])
    with pytest.raises(urllib.error.HTTPError):
        adapter._with_retry(fetch, max_retries=2)
    assert fetch.calls == 3


def test_openalex_search_survives_a_429(monkeypatch, adapter):
    page = {
        "results": [{"id": "https://openalex.org/W1", "title": "Vibe coding",
                     "relevance_score": 10.0}],
        "meta": {"next_cursor": None},
    }
    responses = [_http_error(429), io.BytesIO(json.dumps(page).encode())]

    def fake_urlopen(req, timeout=30):
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    records = list(adapter.search("vibe coding"))
    assert [r.source_id for r in records] == ["W1"]
    assert adapter.errors_during_run == []
    assert adapter.waits == [2.0]
