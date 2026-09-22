"""Unit tests for OA candidate collection and not-downloaded reporting."""
from pathlib import Path

from slr_engine.oa_resolver import (
    direct_download_url,
    extract_arxiv_id,
    resolve_candidates,
    _rank_and_dedupe,
    _collect_arxiv,
    _collect_openalex,
)
from slr_engine.not_downloaded import suggested_action, write_not_downloaded_report


def test_extract_arxiv_id_from_url_and_doi():
    assert extract_arxiv_id(record_url="https://arxiv.org/abs/2401.12345") == "2401.12345"
    assert extract_arxiv_id(record_url="https://arxiv.org/pdf/2401.12345.pdf") == "2401.12345"
    assert extract_arxiv_id(doi="10.48550/arXiv.2401.12345") == "2401.12345"
    assert extract_arxiv_id(source="arxiv", source_id="2401.12345v2") == "2401.12345"
    assert extract_arxiv_id(doi="https://doi.org/10.48550/arXiv.2401.12345") == "2401.12345"
    assert extract_arxiv_id(source_id="arXiv:2401.12345v3") == "2401.12345"
    assert extract_arxiv_id(
        record_url="http://export.arxiv.org/abs/hep-th/9901001v1"
    ) == "hep-th/9901001"


def test_extract_arxiv_id_ignores_lookalikes_in_other_identifiers():
    # Journal DOIs: the first would build a 404, the second a real, unrelated
    # arXiv paper (2010.01234 is a valid October 2020 id).
    assert extract_arxiv_id(doi="10.1016/j.nedt.2026.107278") is None
    assert extract_arxiv_id(doi="10.1016/j.jss.2010.012345") is None
    assert extract_arxiv_id(
        record_url="https://doi.org/10.36227/techrxiv.174681482.27435614/v1"
    ) is None
    # DSpace repository handle.
    assert extract_arxiv_id(
        record_url="https://libeldoc.bsuir.by/handle/123456789/64653"
    ) is None
    assert extract_arxiv_id(source="openalex", source_id="W4410251856") is None


def test_collect_arxiv_skips_journal_dois():
    cands = _collect_arxiv(
        source="openalex", source_id="W1", record_url=None,
        doi="10.1016/j.jss.2010.012345",
    )
    assert cands == []


def test_osf_download_links_point_at_the_file():
    assert direct_download_url("https://osf.io/kjz9t_v1/download") == \
        "https://osf.io/download/kjz9t_v1/"
    assert direct_download_url("https://osf.io/2nu8r/download/") == \
        "https://osf.io/download/2nu8r/"
    assert direct_download_url("https://osf.io/download/kjz9t_v1/") == \
        "https://osf.io/download/kjz9t_v1/"
    assert direct_download_url("https://example.org/paper.pdf") == \
        "https://example.org/paper.pdf"
    ranked = _rank_and_dedupe([
        {"resolver_source": "openalex", "url": "https://osf.io/kjz9t_v1/download",
         "oa_status": "green"},
    ])
    assert ranked[0]["url"] == "https://osf.io/download/kjz9t_v1/"


def test_rank_and_dedupe_prefers_pdf_and_dedupes_urls():
    ranked = _rank_and_dedupe([
        {
            "resolver_source": "openalex",
            "url": "https://example.org/paper",
            "file_format": "html",
            "oa_status": "gold",
            "version_rank": 0,
        },
        {
            "resolver_source": "openalex",
            "url": "https://example.org/paper/",  # same after normalize
            "file_format": "html",
            "oa_status": "gold",
            "version_rank": 0,
        },
        {
            "resolver_source": "pmc",
            "url": "https://ncbi.nlm.nih.gov/pmc/articles/PMC1/pdf/",
            "file_format": "pdf",
            "oa_status": "gold",
            "version_rank": 0,
        },
        {
            "resolver_source": "unpaywall",
            "url": "https://repo.example/aam.pdf",
            "file_format": "pdf",
            "oa_status": "green",
            "version_rank": 1,
        },
    ])
    assert len(ranked) == 3
    assert ranked[0]["resolver_source"] == "pmc"
    assert ranked[0]["file_format"] == "pdf"


def test_openalex_multi_location(monkeypatch):
    payload = {
        "open_access": {"oa_status": "green"},
        "best_oa_location": {
            "is_oa": True,
            "pdf_url": "https://ex.org/best.pdf",
            "license": "cc-by",
            "version": "publishedVersion",
        },
        "oa_locations": [
            {
                "is_oa": True,
                "pdf_url": "https://ex.org/aam.pdf",
                "license": "cc-by",
                "version": "acceptedVersion",
            },
            {
                "is_oa": False,
                "pdf_url": "https://ex.org/closed.pdf",
            },
        ],
    }
    monkeypatch.setattr(
        "slr_engine.oa_resolver._get_json", lambda *a, **k: payload
    )
    locs = _collect_openalex(
        openalex_id="W123", doi=None, contact_email=None, api_key=None,
    )
    urls = {c["url"] for c in locs}
    assert "https://ex.org/best.pdf" in urls
    assert "https://ex.org/aam.pdf" in urls
    assert "https://ex.org/closed.pdf" not in urls


def test_openalex_landing_page_is_a_last_resort(monkeypatch):
    payload = {
        "open_access": {"oa_status": "gold"},
        "best_oa_location": {
            "is_oa": True,
            "pdf_url": None,
            "landing_page_url": "https://doi.org/10.1145/3786583.3786866",
            "license": "cc-by",
            "version": "publishedVersion",
        },
        "oa_locations": [],
    }
    monkeypatch.setattr("slr_engine.oa_resolver._get_json", lambda *a, **k: payload)
    locs = _collect_openalex(
        openalex_id="W4414806416", doi=None, contact_email=None, api_key=None,
    )
    assert [(c["resolver_source"], c["file_format"]) for c in locs] == [
        ("openalex_landing", "html"),
    ]
    ranked = _rank_and_dedupe(locs + [{
        "resolver_source": "arxiv", "url": "https://arxiv.org/pdf/2510.00001.pdf",
        "file_format": "pdf", "oa_status": "green", "version_rank": 1,
    }])
    assert [c["resolver_source"] for c in ranked] == ["arxiv", "openalex_landing"]


def test_unpaywall_version_ranking(monkeypatch):
    payload = {
        "oa_status": "green",
        "best_oa_location": {
            "url_for_pdf": "https://ex.org/submitted.pdf",
            "version": "submittedVersion",
        },
        "oa_locations": [
            {
                "url_for_pdf": "https://ex.org/accepted.pdf",
                "version": "acceptedVersion",
            },
            {
                "url_for_pdf": "https://ex.org/published.pdf",
                "version": "publishedVersion",
            },
        ],
    }
    monkeypatch.setattr(
        "slr_engine.oa_resolver._get_json", lambda *a, **k: payload
    )
    cands = resolve_candidates(doi="10.1/x", contact_email="a@b.com")
    # After rank: pmc/epmc none; unpaywall published before accepted/submitted
    unpaywall = [c for c in cands if c["resolver_source"] == "unpaywall"]
    assert unpaywall[0]["url"] == "https://ex.org/published.pdf"


def test_suggested_action():
    assert suggested_action(doi="10.1/x", url=None) == "ILL"
    assert suggested_action(doi="10.1016/j.nedt.2026.107278", url=None) == "ILL"
    assert suggested_action(
        doi="10.1145/3786583.3786866", url=None, oa_status="gold",
    ) == "open_access_manual"
    assert suggested_action(doi="10.1/x", url=None, oa_status="closed") == "ILL"
    assert suggested_action(
        doi=None, url="https://arxiv.org/abs/2401.12345",
    ) == "check_preprint"
    assert suggested_action(doi=None, url="https://example.org/p") == "author_request"
    assert suggested_action(doi=None, url=None) == "none_found"


def test_write_not_downloaded_report(tmp_path: Path):
    rows = [{
        "canonical_id": "rec1",
        "title": "A study",
        "year": 2020,
        "doi": "10.1/x",
        "pmid": None,
        "pmcid": None,
        "url": None,
        "download_status": "failed",
        "resolver_source": "openalex",
        "error": "404",
        "suggested_action": "ILL",
    }]
    csv_path, txt_path = write_not_downloaded_report(tmp_path, rows)
    assert csv_path.exists()
    assert txt_path.exists()
    text = txt_path.read_text(encoding="utf-8")
    assert "rec1" in text
    assert "Suggested: ILL" in text
    assert "10.1/x" in csv_path.read_text(encoding="utf-8")
