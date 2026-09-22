"""03_dedup blocks on search problems that are still current, not old ones."""
import importlib.util
import json
import sqlite3
from pathlib import Path

from slr_engine.schema import SCHEMA_SQL

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "03_dedup.py"
_spec = importlib.util.spec_from_file_location("dedup_stage", _SCRIPT)
dedup_stage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dedup_stage)


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _query(conn, query_id, source, n, when, notes=""):
    conn.execute(
        "INSERT INTO queries (query_id, source, query_string, result_count, "
        "notes, executed_at) VALUES (?, ?, 'q', ?, ?, ?)",
        (query_id, source, n, notes, when),
    )


def _silent_zero_event(conn, query_id, when):
    conn.execute(
        "INSERT INTO events (stage, level, message, payload_json, occurred_at) "
        "VALUES ('search', 'error', ?, ?, ?)",
        (f"{query_id}: 0 records ingested AFTER 1 request error(s)",
         json.dumps({"query_id": query_id, "count": 0}), when),
    )


def test_failed_source_blocks_until_it_is_searched_again():
    conn = _conn()
    _query(conn, "openalex_1", "openalex", 233, "2026-09-24 07:23:23")
    _query(conn, "s2_1", "semantic_scholar", 0, "2026-09-24 07:23:38")
    _silent_zero_event(conn, "s2_1", "2026-09-24 07:24:40")
    blocking = dedup_stage._last_search_blocking(conn)
    assert [b["message"].split(":")[0] for b in blocking] == ["semantic_scholar"]

    _query(conn, "s2_2", "semantic_scholar", 200, "2026-09-24 07:26:06",
           notes="1 request error(s), coverage may be incomplete")
    assert dedup_stage._last_search_blocking(conn) == []


def test_zero_from_openalex_blocks_even_without_errors():
    conn = _conn()
    _query(conn, "openalex_1", "openalex", 0, "2026-09-24 07:23:23")
    blocking = dedup_stage._last_search_blocking(conn)
    assert len(blocking) == 1 and blocking[0]["message"].startswith("openalex")


def test_validation_failure_blocks_only_until_the_next_search():
    conn = _conn()
    conn.execute(
        "INSERT INTO events (stage, level, message, occurred_at) VALUES "
        "('search', 'error', 'Pre-flight query validation failed; search aborted', "
        "'2026-09-24 07:00:00')"
    )
    assert len(dedup_stage._last_search_blocking(conn)) == 1
    _query(conn, "openalex_1", "openalex", 233, "2026-09-24 07:23:23")
    assert dedup_stage._last_search_blocking(conn) == []
