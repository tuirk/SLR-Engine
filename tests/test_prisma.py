"""PRISMA flow counts and diagram wording."""
import sqlite3

from slr_engine.dedup import fuzzy_dedup
from slr_engine.prisma import render_canonical, render_expanded
from slr_engine.protocol import _flow_counts
from slr_engine.schema import SCHEMA_SQL
from slr_engine.store import ProjectConfig, insert_source_hit


def _flow(ft_decisions: dict) -> dict:
    return {
        "per_source": {"openalex": 233, "arxiv": 141, "crossref": 75, "seed": 2},
        "source_hits_total": 451,
        "records_total": 379,
        "duplicates_removed": 72,
        "dedup_merges": 66,
        "snowball_links": 0,
        "snowball_by_direction": {},
        "ta_decisions": {"include": 164, "exclude": 204, "unsure": 11},
        "ft_decisions": ft_decisions,
        "downloads": {"success": 94, "failed": 48},
        "download_records": {"success": 94, "not_retrieved": 70},
        "extractions_total": 0,
        "extractions_with_quality": 0,
        "extractions_with_risk_of_bias": 0,
    }


def test_title_abstract_only_run_shows_final_count_as_pending():
    cfg = ProjectConfig(project_id="demo")
    for svg in (render_canonical(cfg, _flow({})), render_expanded(cfg, _flow({}))):
        assert "pending: full-text screening not run" in svg
        assert "(164 included at title/abstract)" in svg
    assert "(awaiting full-text: 94)" in render_canonical(cfg, _flow({}))
    assert "Full-text screened: 0 (awaiting: 94)" in render_expanded(cfg, _flow({}))


def test_partial_full_text_screening_reports_what_is_left():
    cfg = ProjectConfig(project_id="demo")
    svg = render_canonical(cfg, _flow({"include": 50, "exclude": 22, "unsure": 2}))
    assert "n = 74" in svg
    assert "(awaiting full-text: 20)" in svg
    assert "(20 reports still awaiting full-text screening)" in svg
    assert "pending" not in svg


def test_complete_full_text_screening_shows_plain_counts():
    cfg = ProjectConfig(project_id="demo")
    svg = render_canonical(cfg, _flow({"include": 70, "exclude": 24}))
    assert "n = 70" in svg
    assert "awaiting" not in svg
    assert "pending" not in svg


def test_canonical_diagram_places_sources_and_duplicates():
    flow = _flow({})
    flow["per_source"] = {"openalex": 10, "scopus": 5, "seed": 2}
    svg = render_canonical(ProjectConfig(project_id="demo"), flow)
    assert "scopus (manual export): n = 5" in svg
    assert "seed papers (user-supplied): n = 2" in svg
    assert "  seed: n = 2" not in svg
    assert "(removed 72 duplicates)" in svg


def _hit(conn, source, source_id, title):
    insert_source_hit(
        conn, source=source, source_id=source_id, query_id=None, raw={},
        title=title, abstract=None,
        authors=[{"family": "Smith", "given": "A"}], year=2025,
    )


def test_flow_counts_reconcile_identified_duplicates_and_records():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _hit(conn, "openalex", "W1", "Vibe coding in practice: a field study")
    _hit(conn, "arxiv", "2501.00001v1", "Vibe coding in practice - a field study")
    _hit(conn, "crossref", "10.1/other", "A different paper entirely")
    fuzzy_dedup(conn)
    # A hit deleted by the old merge code survives only in dedup_log.
    conn.execute(
        "INSERT INTO dedup_log (canonical_id, merged_source, merged_source_id, "
        "match_method) VALUES ('rec_000001', 'openalex', 'W9', 'fuzzy_title')"
    )

    flow = _flow_counts(conn)

    assert flow["per_source"] == {"arxiv": 1, "crossref": 1, "openalex": 2}
    assert flow["records_total"] == 2
    assert flow["source_hits_total"] == 4
    assert flow["duplicates_removed"] == 2
