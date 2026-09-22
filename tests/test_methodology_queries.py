"""The methods report says when a search failed instead of reporting 0 hits."""
import json
import sqlite3

from slr_engine.protocol import _format_search_strategy, _query_summary
from slr_engine.schema import SCHEMA_SQL


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def test_failed_search_is_annotated_from_the_event_log():
    conn = _conn()
    conn.execute(
        "INSERT INTO queries (query_id, source, query_string, result_count) "
        "VALUES ('semantic_scholar_1', 'semantic_scholar', 'vibe coding', 0)"
    )
    conn.execute(
        "INSERT INTO queries (query_id, source, query_string, result_count) "
        "VALUES ('crossref_1', 'crossref', 'vibe coding', 157)"
    )
    conn.execute(
        "INSERT INTO events (stage, level, message, payload_json) VALUES "
        "('search', 'error', ?, ?)",
        ("semantic_scholar: 0 records ingested AFTER 1 request error(s)",
         json.dumps({"query_id": "semantic_scholar_1", "count": 0})),
    )

    text = _format_search_strategy(_query_summary(conn))

    s2, crossref = text.split("**crossref**")
    assert "> Note: semantic_scholar: 0 records ingested AFTER 1 request error(s)" in s2
    assert "Note:" not in crossref


def test_note_stored_with_the_query_wins():
    conn = _conn()
    conn.execute(
        "INSERT INTO queries (query_id, source, query_string, result_count, notes) "
        "VALUES ('openalex_1', 'openalex', 'x', 200, '1 request error(s), coverage may be incomplete')"
    )
    text = _format_search_strategy(_query_summary(conn))
    assert "> Note: 1 request error(s), coverage may be incomplete" in text
