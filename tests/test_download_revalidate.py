"""Stage 06 --revalidate: false successes are demoted and their siblings retried."""
import importlib.util
from pathlib import Path

from slr_engine.store import ProjectPaths, connect, init_db

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "06_download.py"
_spec = importlib.util.spec_from_file_location("download_stage", _SCRIPT)
download_stage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(download_stage)

CHALLENGE = (b"<html><head><title>Client Challenge</title></head><body>"
             b"<noscript>JavaScript is disabled in your browser.</noscript>"
             b"</body></html>")
PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"


def _project(tmp_path: Path, downloads: list[tuple]) -> ProjectPaths:
    paths = ProjectPaths(tmp_path / "proj")
    paths.ensure()
    init_db(paths.db)
    with connect(paths.db) as conn:
        for rid in sorted({d[0] for d in downloads}):
            conn.execute(
                "INSERT INTO records (id, canonical_id, title, title_norm) "
                "VALUES (?, ?, 'A study', 'a study')",
                (rid, f"rec_{rid:06d}"),
            )
        conn.executemany(
            "INSERT INTO downloads (record_id, resolver_source, url, file_format, "
            "status, file_path) VALUES (?, ?, ?, ?, ?, ?)",
            downloads,
        )
    return paths


def _statuses(paths: ProjectPaths) -> dict:
    with connect(paths.db) as conn:
        return {r["url"]: (r["status"], r["error"], r["file_path"])
                for r in conn.execute("SELECT url, status, error, file_path FROM downloads")}


def test_revalidate_demotes_challenge_page_and_requeues_superseded(tmp_path, capsys):
    paths = _project(tmp_path, [
        (1, "crossref", "https://pub.example/1.pdf", "pdf", "failed", None),
        (1, "crossref", "https://pub.example/1", "html", "success",
         "data/fulltext/rec_000001.html"),
        (1, "crossref", "https://pub.example/1.xml", "xml", "skipped_superseded", None),
        (2, "arxiv", "https://arxiv.org/pdf/2401.00001.pdf", "pdf", "success",
         "data/fulltext/rec_000002.pdf"),
    ])
    (paths.fulltext / "rec_000001.html").write_bytes(CHALLENGE)
    (paths.fulltext / "rec_000002.pdf").write_bytes(PDF)

    download_stage._revalidate(paths)

    s = _statuses(paths)
    status, error, file_path = s["https://pub.example/1"]
    assert status == "failed"
    assert "Client Challenge" in error
    assert file_path is None
    assert s["https://pub.example/1.xml"][0] == "resolved"
    assert s["https://pub.example/1.pdf"][0] == "failed"
    assert s["https://arxiv.org/pdf/2401.00001.pdf"][0] == "success"
    assert not (paths.fulltext / "rec_000001.html").exists()
    assert (paths.fulltext / "rec_000002.pdf").exists()
    assert "1 kept, 1 rejected, 0 missing" in capsys.readouterr().out


def test_revalidate_requeues_success_whose_file_is_missing(tmp_path):
    paths = _project(tmp_path, [
        (3, "openalex", "https://repo.example/3.pdf", "pdf", "success",
         "data/fulltext/rec_000003.pdf"),
        (3, "crossref", "https://pub.example/3", "html", "skipped_superseded", None),
    ])

    download_stage._revalidate(paths)

    s = _statuses(paths)
    assert s["https://repo.example/3.pdf"][0] == "resolved"
    assert s["https://pub.example/3"][0] == "queued"


# A long publisher page (menus, abstract, citation widgets) that would pass the
# plain-HTML check: landing pages must not count however much text they carry.
LONG_LANDING_PAGE = (b"<html><head><title>Paper | Publisher</title></head><body><p>"
                     + b"Menu Journals Subscribe Cite this article Share Download PDF " * 400
                     + b"</p></body></html>")


def test_revalidate_rejects_landing_page_successes(tmp_path):
    paths = _project(tmp_path, [
        (4, "openalex_landing", "https://doi.org/10.1/x", "html", "success",
         "data/fulltext/rec_000004.html"),
    ])
    (paths.fulltext / "rec_000004.html").write_bytes(LONG_LANDING_PAGE)

    download_stage._revalidate(paths)

    status, error, _ = _statuses(paths)["https://doi.org/10.1/x"]
    assert status == "failed"
    assert download_stage.LANDING_REASON in error
    assert not (paths.fulltext / "rec_000004.html").exists()


def test_download_never_fetches_landing_pages(tmp_path, monkeypatch):
    paths = _project(tmp_path, [
        (5, "openalex_landing", "https://doi.org/10.1/y", "html", "resolved", None),
    ])
    with connect(paths.db) as conn:
        conn.execute("UPDATE records SET oa_status='gold' WHERE id=5")
    fetched = []
    monkeypatch.setattr(download_stage.urllib.request, "urlopen",
                        lambda req, timeout=60: fetched.append(req.full_url))
    monkeypatch.setattr("sys.argv", ["06_download.py", "--project", "proj",
                                     "--projects-root", str(tmp_path), "--sleep", "0"])

    download_stage.main()

    assert fetched == []
    status, error, _ = _statuses(paths)["https://doi.org/10.1/y"]
    assert (status, error) == ("failed", download_stage.LANDING_REASON)
