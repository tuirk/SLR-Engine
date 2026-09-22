from slr_engine.query_validator import (
    validate_openalex,
    validate_semantic_scholar,
    has_blocking_errors,
    validate_all,
)


def test_quoted_acronym_counts_as_token():
    q = '("LLM" OR "large language model") AND ("code review" OR "pull request")'
    r = validate_openalex(q)
    assert not r.has_errors, r.errors


def test_empty_and_group_still_errors():
    q = '("LLM") AND () AND (review)'
    r = validate_openalex(q)
    assert r.has_errors


def test_semantic_scholar_accepts_plain_text():
    r = validate_semantic_scholar("vibe coding")
    assert not r.has_errors and not r.has_warnings


def test_semantic_scholar_rejects_openalex_style_boolean_query():
    r = validate_semantic_scholar('("vibe coding" OR "vibe-coding") AND ("LLM")')
    assert r.has_errors
    assert any("Boolean" in e for e in r.errors)
    assert any("vibe-coding" in e for e in r.errors)


def test_semantic_scholar_flags_long_queries():
    r = validate_semantic_scholar(
        "vibe coding large language model assisted programming novice "
        "developers software engineering education"
    )
    assert not r.has_errors
    assert r.has_warnings
