import sqlite3

from slr_engine.schema import SCHEMA_SQL
from slr_engine.store import _migrate_schema, insert_source_hit


def _fresh_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def test_migrate_schema_adds_relevance_columns_to_old_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Simulate a pre-existing project.db from before relevance scoring existed.
    conn.executescript(
        """
        CREATE TABLE records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            title_norm TEXT NOT NULL
        );
        """
    )
    cols_before = {row[1] for row in conn.execute("PRAGMA table_info(records)")}
    assert "relevance_score" not in cols_before

    _migrate_schema(conn)

    cols_after = {row[1] for row in conn.execute("PRAGMA table_info(records)")}
    assert "relevance_score" in cols_after
    assert "native_relevance_score" in cols_after

    # Idempotent: running again on an already-migrated db must not raise.
    _migrate_schema(conn)


def test_insert_source_hit_stores_native_relevance_score():
    conn = _fresh_conn()
    record_id = insert_source_hit(
        conn,
        source="openalex",
        source_id="W123",
        query_id=None,
        raw={},
        title="A Survey of Vibe Coding",
        abstract="abstract text",
        authors=[],
        year=2025,
        native_relevance_score=42.5,
    )
    row = conn.execute(
        "SELECT native_relevance_score FROM records WHERE id = ?", (record_id,)
    ).fetchone()
    assert row["native_relevance_score"] == 42.5


def test_insert_source_hit_keeps_max_relevance_score_across_sources():
    conn = _fresh_conn()
    insert_source_hit(
        conn, source="openalex", source_id="W1", query_id=None, raw={},
        title="Same Paper", abstract=None, authors=[], year=2025,
        doi="10.1/x", native_relevance_score=10.0,
    )
    record_id = insert_source_hit(
        conn, source="crossref", source_id="10.1/x", query_id=None, raw={},
        title="Same Paper", abstract=None, authors=[], year=2025,
        doi="10.1/x", native_relevance_score=99.0,
    )
    row = conn.execute(
        "SELECT native_relevance_score FROM records WHERE id = ?", (record_id,)
    ).fetchone()
    assert row["native_relevance_score"] == 99.0

    # A subsequent lower score must not overwrite the higher one already stored.
    insert_source_hit(
        conn, source="semantic_scholar", source_id="s1", query_id=None, raw={},
        title="Same Paper", abstract=None, authors=[], year=2025,
        doi="10.1/x", native_relevance_score=5.0,
    )
    row = conn.execute(
        "SELECT native_relevance_score FROM records WHERE id = ?", (record_id,)
    ).fetchone()
    assert row["native_relevance_score"] == 99.0
