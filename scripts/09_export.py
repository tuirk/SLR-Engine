#!/usr/bin/env python3
"""Export the project as CSV / JSONL / RIS plus audit log.

Outputs:
  exports/records.csv         — all records with screening labels
  exports/records.jsonl       — same, structured
  exports/included.ris        — only screening='include' records, RIS format
  exports/audit.json          — queries, dedup decisions, screening counts

Usage:
  python scripts/09_export.py --project <id>
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slr_engine.store import ProjectConfig, ProjectPaths, connect


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument(
        "--allow-missing-risk-of-bias",
        action="store_true",
        help="Export even if included records lack PRISMA risk-of-bias data.",
    )
    ap.add_argument(
        "--projects-root",
        default=str(Path(__file__).resolve().parents[1] / "projects"),
    )
    args = ap.parse_args()

    project_dir = Path(args.projects_root) / args.project
    cfg = ProjectConfig.load(project_dir)
    paths = ProjectPaths(project_dir)
    paths.exports.mkdir(parents=True, exist_ok=True)

    with connect(paths.db) as conn:
        # Prefer human decisions; fall back to agent; fall back to LLM
        # recommendation. Done with correlated subqueries to avoid Cartesian
        # blowups across decided_by values.
        rows = conn.execute("""
            SELECT r.canonical_id, r.title, r.abstract, r.year, r.first_author,
                   r.authors_json, r.venue, r.doi, r.pmid, r.openalex_id,
                   r.language, r.document_type, r.url,
                   (SELECT decision FROM screening
                      WHERE record_id=r.id AND pass='title_abstract'
                      ORDER BY CASE decided_by
                        WHEN 'human' THEN 1
                        WHEN 'agent' THEN 2
                        ELSE 3 END LIMIT 1) AS ta_decision,
                   (SELECT reason FROM screening
                      WHERE record_id=r.id AND pass='title_abstract'
                      ORDER BY CASE decided_by
                        WHEN 'human' THEN 1
                        WHEN 'agent' THEN 2
                        ELSE 3 END LIMIT 1) AS ta_reason,
                   (SELECT criteria_hit FROM screening
                      WHERE record_id=r.id AND pass='title_abstract'
                      ORDER BY CASE decided_by
                        WHEN 'human' THEN 1
                        WHEN 'agent' THEN 2
                        ELSE 3 END LIMIT 1) AS ta_criteria_hit,
                   (SELECT decided_by FROM screening
                      WHERE record_id=r.id AND pass='title_abstract'
                      ORDER BY CASE decided_by
                        WHEN 'human' THEN 1
                        WHEN 'agent' THEN 2
                        ELSE 3 END LIMIT 1) AS ta_decided_by,
                   (SELECT decision FROM screening
                      WHERE record_id=r.id AND pass='full_text'
                      ORDER BY CASE decided_by
                        WHEN 'human' THEN 1
                        WHEN 'agent' THEN 2
                        ELSE 3 END LIMIT 1) AS ft_decision,
                   (SELECT reason FROM screening
                      WHERE record_id=r.id AND pass='full_text'
                      ORDER BY CASE decided_by
                        WHEN 'human' THEN 1
                        WHEN 'agent' THEN 2
                        ELSE 3 END LIMIT 1) AS ft_reason,
                   (SELECT criteria_hit FROM screening
                      WHERE record_id=r.id AND pass='full_text'
                      ORDER BY CASE decided_by
                        WHEN 'human' THEN 1
                        WHEN 'agent' THEN 2
                        ELSE 3 END LIMIT 1) AS ft_criteria_hit,
                   (SELECT decided_by FROM screening
                      WHERE record_id=r.id AND pass='full_text'
                      ORDER BY CASE decided_by
                        WHEN 'human' THEN 1
                        WHEN 'agent' THEN 2
                        ELSE 3 END LIMIT 1) AS ft_decided_by,
                   d.status AS download_status, d.file_path, d.resolver_source,
                   GROUP_CONCAT(DISTINCT sh.source) AS sources,
                   r.id AS _rid
            FROM records r
            -- One row per record: the success if there is one, otherwise the
            -- same row not_downloaded.csv reports.
            LEFT JOIN downloads d ON d.id = (
                SELECT d2.id FROM downloads d2
                WHERE d2.record_id = r.id
                ORDER BY CASE d2.status
                           WHEN 'success' THEN 0
                           WHEN 'failed' THEN 1
                           WHEN 'skipped_closed' THEN 2
                           WHEN 'resolved' THEN 3
                           WHEN 'queued' THEN 4
                           ELSE 5 END,
                         d2.id DESC
                LIMIT 1
            )
            LEFT JOIN source_hits sh ON sh.record_id = r.id
            GROUP BY r.id
            ORDER BY r.id
        """).fetchall()

        queries = conn.execute(
            "SELECT * FROM queries ORDER BY executed_at"
        ).fetchall()

        dedup = conn.execute(
            "SELECT * FROM dedup_log ORDER BY decided_at"
        ).fetchall()

        events = conn.execute(
            "SELECT stage, level, message, payload_json, occurred_at "
            "FROM events ORDER BY occurred_at"
        ).fetchall()

        snowball_links = conn.execute(
            "SELECT sl.direction, sl.iteration, "
            "  rs.canonical_id AS seed_canonical, "
            "  rf.canonical_id AS found_canonical, "
            "  sl.discovered_at "
            "FROM snowball_links sl "
            "JOIN records rs ON rs.id = sl.seed_record_id "
            "JOIN records rf ON rf.id = sl.found_record_id "
            "ORDER BY sl.discovered_at"
        ).fetchall()

        rob_status = _risk_of_bias_status(conn)

        screen_counts = {"title_abstract": {}, "full_text": {}}
        for r in conn.execute(
            "SELECT pass, decision, COUNT(*) AS n FROM screening "
            "GROUP BY pass, decision"
        ).fetchall():
            screen_counts.setdefault(r["pass"], {})[r["decision"]] = r["n"]

    if rob_status["included"] and rob_status["missing"] and not args.allow_missing_risk_of_bias:
        print("=" * 70)
        print("BLOCKED: included records are missing PRISMA risk-of-bias assessment.")
        print("=" * 70)
        print(f"Included records:         {rob_status['included']}")
        print(f"With risk-of-bias data:   {rob_status['assessed']}")
        print(f"Missing risk-of-bias data:{rob_status['missing']}")
        print()
        print("PRISMA 2020 expects study risk-of-bias methods and per-study")
        print("risk-of-bias results for systematic reviews.")
        print()
        print("Run one of these before export:")
        print(f"  python scripts/07c_llm_fulltext.py --project {args.project} --batch <ft_batch> --with-quality")
        print(f"  python scripts/08b_quality_pass.py --project {args.project}")
        print()
        print("If this is a non-publication practical scan and you intentionally")
        print("accept missing RoB data, rerun export with:")
        print(f"  python scripts/09_export.py --project {args.project} --allow-missing-risk-of-bias")
        sys.exit(2)

    # --- records.csv ---
    csv_path = paths.exports / "records.csv"
    fields = ["canonical_id", "title", "year", "first_author", "venue",
              "doi", "pmid", "openalex_id", "language", "document_type",
              "url", "sources",
              "ta_decision", "ta_reason", "ta_criteria_hit", "ta_decided_by",
              "ft_decision", "ft_reason", "ft_criteria_hit", "ft_decided_by",
              "download_status", "file_path", "resolver_source", "abstract"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] if k in r.keys() else "" for k in fields})

    # --- records.jsonl ---
    jsonl_path = paths.exports / "records.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in rows:
            obj = {k: r[k] for k in r.keys()}
            if obj.get("authors_json"):
                obj["authors"] = json.loads(obj["authors_json"])
                del obj["authors_json"]
            for cf in ("ta_criteria_hit", "ft_criteria_hit"):
                if obj.get(cf):
                    try:
                        obj[cf] = json.loads(obj[cf])
                    except Exception:
                        pass
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    # --- extractions.csv ---
    # One row per (record, extracted_by) so multiple extraction passes are
    # all visible. Includes flattened extraction fields plus quality columns.
    ext_csv = paths.exports / "extractions.csv"
    with connect(paths.db) as conn:
        ext_rows = conn.execute("""
            SELECT r.canonical_id, r.title, r.year, r.doi,
                   e.fields_json, e.methodological_rigor, e.evidence_strength,
                   e.limitations_acknowledged, e.quality_notes,
                   e.risk_of_bias_tool, e.risk_of_bias_overall,
                   e.risk_of_bias_domains_json, e.risk_of_bias_notes,
                   e.extracted_by, e.extracted_at, e.source_text_chars
            FROM extractions e
            JOIN records r ON r.id = e.record_id
            ORDER BY r.id, e.extracted_at
        """).fetchall()

    if ext_rows:
        # Discover the union of extraction field names across all rows
        field_names: set[str] = set()
        for er in ext_rows:
            try:
                fobj = json.loads(er["fields_json"]) if er["fields_json"] else {}
                field_names.update(fobj.keys())
            except Exception:
                pass
        field_names_sorted = sorted(field_names)

        with open(ext_csv, "w", newline="", encoding="utf-8") as f:
            base_cols = ["canonical_id", "title", "year", "doi",
                         "extracted_by", "extracted_at", "source_text_chars",
                         "risk_of_bias_tool", "risk_of_bias_overall",
                         "risk_of_bias_domains_json", "risk_of_bias_notes",
                         "methodological_rigor", "evidence_strength",
                         "limitations_acknowledged", "quality_notes"]
            cols = base_cols + field_names_sorted
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for er in ext_rows:
                try:
                    fobj = json.loads(er["fields_json"]) if er["fields_json"] else {}
                except Exception:
                    fobj = {}
                row = {c: er[c] if c in er.keys() else "" for c in base_cols}
                for fn in field_names_sorted:
                    val = fobj.get(fn, "")
                    if isinstance(val, (list, dict)):
                        val = json.dumps(val, ensure_ascii=False)
                    row[fn] = val
                w.writerow(row)

    # --- included.ris ---
    # If a record has been through full-text screening, that's the final word.
    # Otherwise fall back to title/abstract decision.
    ris_path = paths.exports / "included.ris"
    with open(ris_path, "w", encoding="utf-8") as f:
        for r in rows:
            final = r["ft_decision"] if r["ft_decision"] else r["ta_decision"]
            if final != "include":
                continue
            f.write(_to_ris(r))
            f.write("\n")

    # --- audit.json ---
    audit_path = paths.exports / "audit.json"
    audit = {
        "project_id": cfg.project_id,
        "question": cfg.question,
        "date_range": {"from": cfg.date_from, "to": cfg.date_to},
        "languages": cfg.languages,
        "sources": cfg.sources,
        "screening_counts": screen_counts,
        "totals": {
            "records": len(rows),
            "queries_run": len(queries),
            "dedup_merges": len(dedup),
            "snowball_links": len(snowball_links),
            "risk_of_bias_included": rob_status,
        },
        "queries": [dict(q) for q in queries],
        "dedup_decisions": [dict(d) for d in dedup],
        "snowball_links": [dict(s) for s in snowball_links],
        "events": [dict(e) for e in events],
    }
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2, default=str)

    # --- methodology_report.md ---
    from slr_engine.protocol import write_methodology_report
    report_path = write_methodology_report(cfg, paths)

    # --- PRISMA flow diagrams ---
    from slr_engine.prisma import write_diagrams
    canonical_path, expanded_path = write_diagrams(cfg, paths)

    print(f"Wrote: {csv_path}")
    print(f"       {jsonl_path}")
    if ext_rows:
        print(f"       {ext_csv}  ({len(ext_rows)} extraction rows)")
    print(f"       {ris_path}")
    print(f"       {audit_path}")
    print(f"       {report_path}")
    print(f"       {canonical_path}     (PRISMA 2020 — for publication)")
    print(f"       {expanded_path}  (engine-aware — for full audit)")
    print()
    print("Screening counts:", screen_counts)
    print("Total records:   ", len(rows))


_RIS_TYPES = {
    "journal-article": "JOUR", "article": "JOUR", "journalarticle": "JOUR",
    "review": "JOUR", "editorial": "JOUR", "letter": "JOUR",
    "conference-paper": "CPAPER", "proceedings-article": "CPAPER",
    "conference": "CPAPER", "conference-abstract": "CPAPER",
    "preprint": "UNPB", "posted-content": "UNPB",
    "book-chapter": "CHAP", "book": "BOOK", "monograph": "BOOK",
    "dissertation": "THES", "thesis": "THES",
    "report": "RPRT", "dataset": "DATA",
}
_CONFERENCE_VENUE = re.compile(
    r"conference|proceedings|workshop|symposium|congress|\bCHI\b|\bICSE\b", re.I
)
_PREPRINT_VENUE = re.compile(r"arxiv|ssrn|research square|techrxiv|preprint", re.I)


def _ris_type(document_type, venue) -> str:
    """RIS item type from the source's document type, checked against the venue.

    Semantic Scholar labels many conference papers "JournalArticle" or "Book",
    so a conference-looking venue overrides those labels.
    """
    ris = _RIS_TYPES.get((document_type or "").lower().replace(" ", ""))
    venue = venue or ""
    if ris in (None, "JOUR", "BOOK") and _CONFERENCE_VENUE.search(venue):
        return "CPAPER"
    if ris is None:
        return "UNPB" if _PREPRINT_VENUE.search(venue) else "JOUR"
    return ris


def _to_ris(r) -> str:
    """Minimal RIS for an included record."""
    ris_type = _ris_type(r["document_type"], r["venue"])
    lines = [f"TY  - {ris_type}"]
    if r["title"]:
        lines.append(f"TI  - {r['title']}")
    if r["authors_json"]:
        try:
            for a in json.loads(r["authors_json"]):
                fam = a.get("family", "")
                giv = a.get("given", "")
                lines.append(f"AU  - {fam}, {giv}".rstrip(", "))
        except Exception:
            pass
    if r["year"]:
        lines.append(f"PY  - {r['year']}")
    if r["venue"]:
        # Journal name for articles; proceedings, book or repository otherwise.
        lines.append(f"{'JO' if ris_type == 'JOUR' else 'T2'}  - {r['venue']}")
    if r["doi"]:
        lines.append(f"DO  - {r['doi']}")
    if r["url"]:
        lines.append(f"UR  - {r['url']}")
    if r["abstract"]:
        lines.append(f"AB  - {r['abstract']}")
    if r["language"]:
        lines.append(f"LA  - {r['language']}")
    lines.append("ER  - ")
    return "\n".join(lines) + "\n"


def _risk_of_bias_status(conn) -> dict:
    """Count final included records with/without risk-of-bias data."""
    included_rows = conn.execute(
        """
        SELECT r.id
        FROM records r
        WHERE COALESCE(
            (SELECT decision FROM screening
             WHERE record_id=r.id AND pass='full_text'
             ORDER BY CASE decided_by
               WHEN 'human' THEN 1
               WHEN 'agent' THEN 2
               ELSE 3 END LIMIT 1),
            (SELECT decision FROM screening
             WHERE record_id=r.id AND pass='title_abstract'
             ORDER BY CASE decided_by
               WHEN 'human' THEN 1
               WHEN 'agent' THEN 2
               ELSE 3 END LIMIT 1)
        ) = 'include'
        """
    ).fetchall()
    included_ids = [r["id"] for r in included_rows]
    if not included_ids:
        return {"included": 0, "assessed": 0, "missing": 0}
    placeholders = ",".join("?" for _ in included_ids)
    assessed = conn.execute(
        f"""
        SELECT COUNT(DISTINCT record_id) AS n
        FROM extractions
        WHERE record_id IN ({placeholders})
          AND risk_of_bias_overall IS NOT NULL
          AND risk_of_bias_overall != ''
        """,
        included_ids,
    ).fetchone()["n"]
    return {
        "included": len(included_ids),
        "assessed": int(assessed or 0),
        "missing": len(included_ids) - int(assessed or 0),
    }


if __name__ == "__main__":
    main()
