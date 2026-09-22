"""Structured report of included records without successful full-text download."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from .oa_resolver import ALLOWED_OA_TIERS, extract_arxiv_id

REPORT_FIELDS = [
    "canonical_id",
    "title",
    "year",
    "doi",
    "pmid",
    "pmcid",
    "url",
    "oa_url",
    "download_status",
    "resolver_source",
    "error",
    "suggested_action",
]


def suggested_action(
    *,
    doi: Optional[str],
    url: Optional[str],
    source: Optional[str] = None,
    source_id: Optional[str] = None,
    oa_status: Optional[str] = None,
) -> str:
    if extract_arxiv_id(source=source, source_id=source_id, record_url=url, doi=doi):
        return "check_preprint"
    if (oa_status or "").lower() in ALLOWED_OA_TIERS:
        # Open access, but the script could not fetch it (bot challenge,
        # landing page only): a browser usually can.
        return "open_access_manual"
    if doi:
        return "ILL"
    if url:
        return "author_request"
    return "none_found"


def fetch_not_downloaded(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Title/abstract includes that have no successful download row."""
    return conn.execute(
        """
        SELECT r.id, r.canonical_id, r.title, r.year, r.doi, r.pmid, r.pmcid,
               r.url, r.oa_status, r.oa_url,
               (SELECT sh.source FROM source_hits sh
                 WHERE sh.record_id = r.id AND sh.source = 'arxiv'
                 LIMIT 1) AS source,
               (SELECT sh.source_id FROM source_hits sh
                 WHERE sh.record_id = r.id AND sh.source = 'arxiv'
                 LIMIT 1) AS source_id,
               d.status AS download_status,
               d.resolver_source,
               d.error
        FROM records r
        JOIN screening s_ta ON s_ta.record_id = r.id
             AND s_ta.pass='title_abstract' AND s_ta.decision='include'
        LEFT JOIN downloads d_ok ON d_ok.record_id = r.id AND d_ok.status='success'
        LEFT JOIN downloads d ON d.id = (
            SELECT d2.id FROM downloads d2
            WHERE d2.record_id = r.id
              AND d2.status IN ('failed', 'skipped_closed', 'resolved', 'queued')
            ORDER BY
              CASE d2.status
                WHEN 'failed' THEN 0
                WHEN 'skipped_closed' THEN 1
                WHEN 'resolved' THEN 2
                WHEN 'queued' THEN 3
                ELSE 4
              END,
              d2.id DESC
            LIMIT 1
        )
        WHERE d_ok.id IS NULL
        ORDER BY r.id
        """
    ).fetchall()


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        keys = set(r.keys())
        out.append({
            "canonical_id": r["canonical_id"],
            "title": r["title"],
            "year": r["year"],
            "doi": r["doi"],
            "pmid": r["pmid"],
            "pmcid": r["pmcid"],
            "url": r["url"],
            "oa_url": (r["oa_url"] if "oa_url" in keys else None) or "",
            "download_status": r["download_status"] or "none",
            "resolver_source": r["resolver_source"] or "",
            "error": r["error"] or "",
            "suggested_action": suggested_action(
                doi=r["doi"],
                url=r["url"],
                source=r["source"] if "source" in keys else None,
                source_id=r["source_id"] if "source_id" in keys else None,
                oa_status=r["oa_status"] if "oa_status" in keys else None,
            ),
        })
    return out


def write_not_downloaded_report(screening_dir: Path, rows: list[dict]) -> tuple[Path, Path]:
    """Write CSV + richer TXT under screening/. Returns (csv_path, txt_path)."""
    screening_dir.mkdir(parents=True, exist_ok=True)
    csv_path = screening_dir / "not_downloaded.csv"
    txt_path = screening_dir / "not_downloaded.txt"

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("# Records included at title/abstract without a downloaded full text.\n")
        f.write("# Use the open-access link, ILL, an author request, or a preprint check.\n")
        f.write(f"# Count: {len(rows)}\n\n")
        for row in rows:
            year = row.get("year") or "?"
            f.write(f"- {row['canonical_id']} ({year}) {row.get('title') or ''}\n")
            if row.get("doi"):
                f.write(f"  DOI: {row['doi']}\n")
                f.write(f"  DOI URL: https://doi.org/{row['doi']}\n")
            if row.get("pmid"):
                f.write(f"  PMID: {row['pmid']}\n")
            if row.get("pmcid"):
                f.write(f"  PMCID: {row['pmcid']}\n")
            if row.get("url"):
                f.write(f"  URL: {row['url']}\n")
            if row.get("oa_url"):
                f.write(f"  Open-access copy: {row['oa_url']}\n")
            status = row.get("download_status") or "none"
            src = row.get("resolver_source") or "-"
            f.write(f"  Status: {status} (via {src})\n")
            if row.get("error"):
                f.write(f"  Error: {row['error']}\n")
            f.write(f"  Suggested: {row.get('suggested_action')}\n\n")

    return csv_path, txt_path
