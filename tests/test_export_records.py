"""records.csv reports each record's real download outcome."""
import csv
import importlib.util
import subprocess
import sys
from pathlib import Path

from slr_engine.store import ProjectPaths, connect, init_project

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("export_stage", REPO / "scripts" / "09_export.py")
export_stage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_stage)


def test_ris_types_follow_document_type_and_venue():
    t = export_stage._ris_type
    assert t("journal-article", "Journal of Computer Languages") == "JOUR"
    assert t("preprint", "cs.SE") == "UNPB"
    assert t("posted-content", None) == "UNPB"
    assert t("book-chapter", "Frontiers in AI and Applications") == "CHAP"
    assert t("dissertation", None) == "THES"
    assert t("conference-paper", None) == "CPAPER"
    # Semantic Scholar's labels for conference papers
    assert t("JournalArticle", "ACM Southeast Conference") == "CPAPER"
    assert t("Book", "International Conference on Human Factors in Computing Systems") == "CPAPER"
    assert t(None, "arXiv (Cornell University)") == "UNPB"
    assert t("other", None) == "JOUR"


def test_records_csv_reports_the_successful_download(tmp_path):
    project_dir = init_project(tmp_path, "demo", topic="vibe coding")
    paths = ProjectPaths(project_dir)
    with connect(paths.db) as conn:
        for rid in (1, 2, 3):
            conn.execute(
                "INSERT INTO records (id, canonical_id, title, title_norm) "
                "VALUES (?, ?, 'A study', 'a study')",
                (rid, f"rec_{rid:06d}"),
            )
            conn.execute(
                "INSERT INTO screening (record_id, pass, decision, decided_by) "
                "VALUES (?, 'title_abstract', 'include', 'agent')",
                (rid,),
            )
        conn.executemany(
            "INSERT INTO downloads (record_id, resolver_source, url, status, "
            "file_path, error) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "arxiv", "https://arxiv.org/pdf/x.pdf", "failed", None, "404"),
                (1, "openalex", "https://repo/1.pdf", "success",
                 "data/fulltext/rec_000001.pdf", None),
                (1, "crossref", "https://pub/1", "skipped_superseded", None, None),
                (2, "crossref", "https://pub/2.pdf", "failed", None, "403"),
                (2, "crossref", "https://pub/2", "failed", None, "challenge page"),
            ],
        )

    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "09_export.py"), "--project", "demo",
         "--projects-root", str(tmp_path), "--allow-missing-risk-of-bias"],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr

    with open(paths.exports / "records.csv", encoding="utf-8") as f:
        rows = {r["canonical_id"]: r for r in csv.DictReader(f)}
    assert rows["rec_000001"]["download_status"] == "success"
    assert rows["rec_000001"]["file_path"] == "data/fulltext/rec_000001.pdf"
    assert rows["rec_000001"]["resolver_source"] == "openalex"
    assert rows["rec_000002"]["download_status"] == "failed"
    assert rows["rec_000003"]["download_status"] == ""
