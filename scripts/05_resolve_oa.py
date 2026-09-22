#!/usr/bin/env python3
"""Resolve open-access full-text URL candidates for included records.

Collects ranked OA candidates (PMC → Europe PMC → OpenAlex → Unpaywall →
arXiv → CORE → Crossref) and stores the best as status='resolved' with
remaining candidates as status='queued'. CORE is used only when
`sources.core: true` in project.yaml and `CORE_API_KEY` is set.

The actual fetch (walking resolved then queued) happens in 06_download.py.

By default, only resolves records with screening decision='include'. Use
--all-screened to also include 'unsure'. Use --retry-failed to clear prior
failed/skipped download rows and re-resolve those records.

Usage:
  python scripts/05_resolve_oa.py --project <id>
  python scripts/05_resolve_oa.py --project <id> --decided-by agent
  python scripts/05_resolve_oa.py --project <id> --retry-failed
"""
import argparse
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slr_engine.env import load_dotenv, openalex_api_key
from slr_engine.store import ProjectConfig, ProjectPaths, connect, log_event
from slr_engine.oa_resolver import resolve_candidates, ALLOWED_OA_TIERS


load_dotenv()


def _clear_failed_downloads(conn, record_ids: list[int] | None = None) -> int:
    """Delete download rows for records that never succeeded (for re-resolve)."""
    if record_ids is not None and not record_ids:
        return 0
    if record_ids is None:
        cur = conn.execute(
            """
            DELETE FROM downloads
            WHERE record_id IN (
              SELECT d.record_id FROM downloads d
              WHERE d.record_id NOT IN (
                SELECT record_id FROM downloads WHERE status = 'success'
              )
            )
            """
        )
    else:
        placeholders = ",".join("?" * len(record_ids))
        cur = conn.execute(
            f"""
            DELETE FROM downloads
            WHERE record_id IN ({placeholders})
              AND record_id NOT IN (
                SELECT record_id FROM downloads WHERE status = 'success'
              )
            """,
            record_ids,
        )
    return cur.rowcount


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--all-screened", action="store_true",
                    help="Also resolve 'unsure' records")
    ap.add_argument("--decided-by", default=None,
                    help="Filter screening rows by decided_by (e.g. agent, human, llm)")
    ap.add_argument("--core-api-key", default=None)
    ap.add_argument(
        "--retry-failed",
        action="store_true",
        help="Clear failed/skipped download rows (no success) and re-resolve",
    )
    ap.add_argument(
        "--projects-root",
        default=str(Path(__file__).resolve().parents[1] / "projects"),
    )
    args = ap.parse_args()

    project_dir = Path(args.projects_root) / args.project
    cfg = ProjectConfig.load(project_dir)
    paths = ProjectPaths(project_dir)

    decisions = ("include",) if not args.all_screened else ("include", "unsure")
    placeholders = ",".join("?" * len(decisions))

    screening_filter = ""
    params: list = list(decisions)
    if args.decided_by:
        screening_filter = " AND s.decided_by = ?"
        params.append(args.decided_by)

    with connect(paths.db) as conn:
        if args.retry_failed:
            n = _clear_failed_downloads(conn)
            print(f"Cleared {n} download row(s) for retry.")

        rows = conn.execute(
            f"SELECT r.id, r.canonical_id, r.doi, r.pmid, r.pmcid, r.openalex_id, "
            f"       r.url, "
            f"       (SELECT sh.source_id FROM source_hits sh "
            f"         WHERE sh.record_id = r.id AND sh.source = 'arxiv' "
            f"         LIMIT 1) AS arxiv_source_id "
            f"FROM records r "
            f"JOIN screening s ON s.record_id = r.id "
            f"WHERE s.pass = 'title_abstract' AND s.decision IN ({placeholders})"
            f"{screening_filter} "
            f"AND r.id NOT IN ("
            f"  SELECT record_id FROM downloads "
            f"  WHERE status IN ('resolved', 'success', 'queued')"
            f") "
            f"ORDER BY r.id",
            params
        ).fetchall()

    print(f"Resolving OA for {len(rows)} records...")
    resolved = 0
    queued_total = 0
    skipped = 0
    lookup_failed = 0
    failed_sources: Counter = Counter()
    oa_key = openalex_api_key(cfg)
    core_key = None
    if cfg.sources.get("core") is True:
        core_key = args.core_api_key or os.environ.get("CORE_API_KEY")

    for r in rows:
        errors: list[str] = []
        candidates = resolve_candidates(
            doi=r["doi"], pmid=r["pmid"], pmcid=r["pmcid"],
            openalex_id=r["openalex_id"],
            contact_email=cfg.contact_email,
            core_api_key=core_key,
            openalex_api_key=oa_key,
            record_url=r["url"],
            source="arxiv" if r["arxiv_source_id"] else None,
            source_id=r["arxiv_source_id"],
            errors=errors,
        )
        # Keep only allowed OA tiers
        candidates = [
            c for c in candidates
            if (c.get("oa_status") or "unknown") in ALLOWED_OA_TIERS
        ]

        failed_sources.update({e.split(":", 1)[0] for e in errors})
        with connect(paths.db) as conn:
            if not candidates and errors:
                # "No open copy" is unproven while a lookup failed; leave the
                # record without download rows so the next run retries it.
                lookup_failed += 1
                log_event(conn, "resolve", "warn",
                          f"OA lookup failed: {r['canonical_id']}",
                          {"errors": errors[:5]})
                print(f"  LOOKUP FAILED {r['canonical_id']}: {errors[0]}")
                continue
            if errors:
                log_event(conn, "resolve", "warn",
                          f"OA lookup incomplete: {r['canonical_id']}",
                          {"errors": errors[:5]})

            # A record re-resolved after an earlier run found nothing.
            conn.execute(
                "DELETE FROM downloads WHERE record_id=? AND status='skipped_closed'",
                (r["id"],),
            )
            if not candidates:
                conn.execute(
                    "INSERT INTO downloads "
                    "(record_id, resolver_source, url, status) "
                    "VALUES (?, 'none', '', 'skipped_closed')",
                    (r["id"],)
                )
                conn.execute(
                    "UPDATE records SET oa_status = 'closed' WHERE id = ?",
                    (r["id"],)
                )
                skipped += 1
                continue

            first = candidates[0]
            oa_status = first.get("oa_status", "unknown")
            conn.execute(
                "INSERT INTO downloads "
                "(record_id, resolver_source, url, license, file_format, status) "
                "VALUES (?,?,?,?,?,'resolved')",
                (r["id"], first["resolver_source"], first["url"],
                 first.get("license"), first.get("file_format"))
            )
            for alt in candidates[1:]:
                conn.execute(
                    "INSERT INTO downloads "
                    "(record_id, resolver_source, url, license, file_format, status) "
                    "VALUES (?,?,?,?,?,'queued')",
                    (r["id"], alt["resolver_source"], alt["url"],
                     alt.get("license"), alt.get("file_format"))
                )
                queued_total += 1
            conn.execute(
                "UPDATE records SET oa_status = ?, oa_url = ?, license = ? "
                "WHERE id = ?",
                (oa_status, first["url"], first.get("license"), r["id"])
            )
            resolved += 1

    print(f"Resolved: {resolved}")
    print(f"Queued alternates: {queued_total}")
    print(f"Skipped (no OA found): {skipped}")
    if failed_sources:
        per_source = ", ".join(f"{s} for {n} record(s)" for s, n in failed_sources.most_common())
        print(f"Lookup errors: {per_source}. Records resolved without those sources may")
        print("  be missing a copy; after downloading, re-run this script and 06 to retry.")
    if lookup_failed:
        print(f"Lookups failed: {lookup_failed} record(s) left unresolved; "
              f"re-run this script to retry them.")
    print()
    print("Next: python scripts/06_download.py --project", args.project)


if __name__ == "__main__":
    main()
