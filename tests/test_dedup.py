"""Fuzzy dedup keeps the merged record's source hits."""
import sqlite3

from slr_engine.dedup import fuzzy_dedup
from slr_engine.schema import SCHEMA_SQL
from slr_engine.store import insert_source_hit


def test_fuzzy_merge_moves_source_hits_to_the_kept_record():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    authors = [{"family": "Smith", "given": "A"}]
    keep = insert_source_hit(
        conn, source="openalex", source_id="W1", query_id=None, raw={},
        title="Vibe coding in practice: a field study", abstract=None,
        authors=authors, year=2025,
    )
    insert_source_hit(
        conn, source="arxiv", source_id="2501.00001v1", query_id=None, raw={},
        title="Vibe coding in practice - a field study", abstract=None,
        authors=authors, year=2025,
    )

    assert fuzzy_dedup(conn)["merges"] == 1

    hits = conn.execute(
        "SELECT source, source_id, record_id FROM source_hits ORDER BY source"
    ).fetchall()
    assert [(h["source"], h["source_id"], h["record_id"]) for h in hits] == [
        ("arxiv", "2501.00001v1", keep),
        ("openalex", "W1", keep),
    ]
    assert conn.execute("SELECT COUNT(*) FROM dedup_log").fetchone()[0] == 1
