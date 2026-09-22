"""Search-failure notes stored with a query read as whole words."""
import importlib.util
import urllib.error
from pathlib import Path

from slr_engine.sources.semantic_scholar import _friendly_error

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "02_search_open.py"
_spec = importlib.util.spec_from_file_location("search_stage", _SCRIPT)
search_stage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(search_stage)


def _s2_rate_limit_message():
    err = urllib.error.HTTPError("https://api.semanticscholar.org/x", 429, "", {}, None)
    return "semantic_scholar request failed: " + _friendly_error(err, api_key_set=False)


def test_rate_limit_advice_is_kept_whole():
    msg = _s2_rate_limit_message()
    note = search_stage._failure_note([msg], 0)
    assert note.startswith("1 request error(s), search failed; 0 is not a real null result: ")
    assert note.endswith("Set S2_API_KEY in .env.")


def test_partial_results_say_coverage_may_be_incomplete():
    note = search_stage._failure_note(["timeout", "timeout"], 200)
    assert note == "2 request error(s), coverage may be incomplete: timeout"


def test_long_errors_end_at_a_word_boundary():
    words = " ".join(f"word{i}" for i in range(200))
    note = search_stage._failure_note([words], 0, limit=100)
    kept = note.split(": ", 1)[1]
    assert kept.endswith(" …")
    assert kept[:-2] in words and words.startswith(kept[:-2] + " ")
