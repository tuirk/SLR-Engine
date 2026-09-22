"""OA lookups tell "no open copy" apart from "could not check"."""
import io
import json
import urllib.error

import pytest

from slr_engine import oa_resolver
from slr_engine.oa_resolver import LookupFailed, _get_json, resolve_candidates


def _http_error(code):
    return urllib.error.HTTPError("https://api.example/x", code, "error", {}, None)


@pytest.fixture(autouse=True)
def fresh_hosts():
    oa_resolver._HOST_FAILURES.clear()
    yield
    oa_resolver._HOST_FAILURES.clear()


@pytest.fixture
def no_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr(oa_resolver.time, "sleep", waits.append)
    return waits


def _urlopen_sequence(monkeypatch, *outcomes):
    outcomes = list(outcomes)

    def fake(req, timeout=30):
        item = outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return io.BytesIO(json.dumps(item).encode())

    monkeypatch.setattr(oa_resolver.urllib.request, "urlopen", fake)


def test_not_found_is_an_answer(monkeypatch, no_sleep):
    _urlopen_sequence(monkeypatch, _http_error(404))
    assert _get_json("https://api.openalex.org/works/doi:10.1/x") is None
    assert no_sleep == []


def test_rate_limit_is_retried(monkeypatch, no_sleep):
    _urlopen_sequence(monkeypatch, _http_error(429), {"ok": True})
    assert _get_json("https://api.openalex.org/works/W1") == {"ok": True}
    assert no_sleep == [2.0]


def test_persistent_failures_raise(monkeypatch, no_sleep):
    _urlopen_sequence(monkeypatch, *[urllib.error.URLError("getaddrinfo failed")] * 4)
    with pytest.raises(LookupFailed, match="api.openalex.org"):
        _get_json("https://api.openalex.org/works/W1")
    assert len(no_sleep) == 3


def test_a_host_that_keeps_failing_is_skipped_for_the_rest_of_the_run(monkeypatch, no_sleep):
    calls = []

    def down(req, timeout=30):
        calls.append(req.full_url)
        raise _http_error(503)

    monkeypatch.setattr(oa_resolver.urllib.request, "urlopen", down)
    for _ in range(oa_resolver._HOST_DOWN_AFTER):
        with pytest.raises(LookupFailed):
            _get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?q=1")
    made = len(calls)
    with pytest.raises(LookupFailed, match="unavailable earlier in this run"):
        _get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?q=2")
    assert len(calls) == made


def test_failed_lookups_are_reported_not_treated_as_closed(monkeypatch):
    def unreachable(*a, **k):
        raise LookupFailed("api.openalex.org: URLError: getaddrinfo failed")

    monkeypatch.setattr(oa_resolver, "_get_json", unreachable)
    errors = []
    cands = resolve_candidates(doi="10.1/x", openalex_id="W1", errors=errors)
    assert cands == []
    assert {e.split(":")[0] for e in errors} == {"europepmc", "openalex", "crossref"}


def test_lookups_that_answer_leave_no_errors(monkeypatch):
    monkeypatch.setattr(oa_resolver, "_get_json", lambda *a, **k: None)
    errors = []
    assert resolve_candidates(doi="10.1/x", errors=errors) == []
    assert errors == []
