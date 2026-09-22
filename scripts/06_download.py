#!/usr/bin/env python3
"""Download OA full-text files for resolved records.

Walks status='resolved' then status='queued' candidates per record until one
download succeeds. Leftover queued rows become skipped_superseded.

A response only counts as a success when its bytes are a usable full text:
bot/JavaScript challenge pages, empty app shells and metadata-only XML are
recorded as failures so the next candidate is tried.

Usage:
  python scripts/06_download.py --project <id>
  python scripts/06_download.py --project <id> --max 50
  python scripts/06_download.py --project <id> --retry-failed
  python scripts/06_download.py --project <id> --revalidate
"""
import argparse
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slr_engine.store import ProjectPaths, connect, log_event
from slr_engine.oa_resolver import ALLOWED_OA_TIERS, LANDING_SOURCES, direct_download_url
from slr_engine.fulltext_validation import EXTENSIONS, check_fulltext
from slr_engine.not_downloaded import (
    fetch_not_downloaded,
    rows_to_dicts,
    write_not_downloaded_report,
)


USER_AGENT = "slr-engine/1.0 (research; OA only)"


def _safe_filename(canonical_id: str, fmt: str) -> str:
    return f"{canonical_id}.{EXTENSIONS.get(fmt, 'bin')}"


# Landing pages are candidates so an open-access work keeps its status and its
# link in not_downloaded.csv, but they are not fetched: publisher and
# repository pages (menus, abstract, citation widgets) cannot be told apart
# from an HTML article reliably, and most refuse scripts anyway.
LANDING_REASON = "open-access landing page, no direct file link: open it in a browser"


def _requeue_superseded(conn, record_id: int) -> None:
    """Put candidates that an earlier success had superseded back in line."""
    conn.execute(
        "UPDATE downloads SET status='queued', error=NULL "
        "WHERE record_id=? AND status='skipped_superseded'",
        (record_id,),
    )
    if conn.execute(
        "SELECT 1 FROM downloads WHERE record_id=? AND status='resolved'",
        (record_id,),
    ).fetchone():
        return
    first = conn.execute(
        "SELECT id FROM downloads WHERE record_id=? AND status='queued' "
        "ORDER BY id LIMIT 1",
        (record_id,),
    ).fetchone()
    if first:
        conn.execute(
            "UPDATE downloads SET status='resolved' WHERE id=?", (first["id"],)
        )


def _revalidate(paths: ProjectPaths) -> None:
    """Re-check files already marked success; demote the ones that are not full text."""
    to_delete: list[Path] = []
    missing = 0
    with connect(paths.db) as conn:
        rows = conn.execute(
            """
            SELECT d.id AS dl_id, d.record_id, d.file_path, d.file_format,
                   d.resolver_source, r.canonical_id
            FROM downloads d JOIN records r ON r.id = d.record_id
            WHERE d.status = 'success'
            ORDER BY d.record_id
            """
        ).fetchall()
        for row in rows:
            path = paths.root / row["file_path"] if row["file_path"] else None
            if path is None or not path.is_file():
                # The URL worked before; fetch it again rather than failing it.
                missing += 1
                conn.execute(
                    "UPDATE downloads SET status='queued', file_path=NULL, "
                    "error='file missing on disk; re-queued' WHERE id=?",
                    (row["dl_id"],),
                )
                _requeue_superseded(conn, row["record_id"])
                print(f"  MISSING {row['canonical_id']}: re-queued")
                continue
            if row["resolver_source"] in LANDING_SOURCES:
                reason = LANDING_REASON
            else:
                reason = check_fulltext(path.read_bytes(), row["file_format"]).reason
            if reason is None:
                continue
            to_delete.append(path)
            conn.execute(
                "UPDATE downloads SET status='failed', file_path=NULL, error=? "
                "WHERE id=?",
                (f"rejected on revalidation: {reason}"[:500], row["dl_id"]),
            )
            _requeue_superseded(conn, row["record_id"])
            log_event(conn, "download", "warn",
                      f"Rejected on revalidation: {row['canonical_id']}",
                      {"file_path": row["file_path"], "reason": reason})
            print(f"  REJECT {path.name}: {reason}")
    for path in to_delete:
        path.unlink(missing_ok=True)
    kept = len(rows) - len(to_delete) - missing
    print(f"Revalidated {len(rows)} download(s): {kept} kept, "
          f"{len(to_delete)} rejected, {missing} missing.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--max", type=int, default=10000,
                    help="Max records (not URLs) to attempt")
    ap.add_argument("--sleep", type=float, default=0.5,
                    help="Seconds between requests (be polite)")
    ap.add_argument(
        "--retry-failed",
        action="store_true",
        help="Re-queue records that only have failed attempts (promote to resolved)",
    )
    ap.add_argument(
        "--revalidate",
        action="store_true",
        help="Re-check files already marked success; delete ones that are not "
             "full text (challenge pages, metadata-only XML) and try the "
             "record's other candidates",
    )
    ap.add_argument(
        "--projects-root",
        default=str(Path(__file__).resolve().parents[1] / "projects"),
    )
    args = ap.parse_args()

    paths = ProjectPaths(Path(args.projects_root) / args.project)
    paths.fulltext.mkdir(parents=True, exist_ok=True)
    paths.screening.mkdir(parents=True, exist_ok=True)

    if args.revalidate:
        _revalidate(paths)

    with connect(paths.db) as conn:
        if args.retry_failed:
            # Promote latest failed row per record (no success) back to resolved
            # and clear sibling failed/queued so 05 can also be re-run cleanly.
            failed_ids = [
                r["record_id"]
                for r in conn.execute(
                    """
                    SELECT DISTINCT d.record_id
                    FROM downloads d
                    WHERE d.status = 'failed'
                      AND d.record_id NOT IN (
                        SELECT record_id FROM downloads WHERE status = 'success'
                      )
                    """
                ).fetchall()
            ]
            for rid in failed_ids:
                # Keep URLs: set all failed/queued/skipped_superseded → queued,
                # then first by id → resolved
                conn.execute(
                    """
                    UPDATE downloads SET status='queued', error=NULL
                    WHERE record_id=? AND status IN
                      ('failed', 'queued', 'skipped_superseded', 'resolved')
                    """,
                    (rid,),
                )
                first = conn.execute(
                    "SELECT id FROM downloads WHERE record_id=? "
                    "AND status='queued' ORDER BY id LIMIT 1",
                    (rid,),
                ).fetchone()
                if first:
                    conn.execute(
                        "UPDATE downloads SET status='resolved' WHERE id=?",
                        (first["id"],),
                    )
            print(f"Re-queued {len(failed_ids)} failed record(s).")

        record_ids = [
            r["record_id"]
            for r in conn.execute(
                """
                SELECT DISTINCT d.record_id
                FROM downloads d
                WHERE d.status IN ('resolved', 'queued')
                  AND d.record_id NOT IN (
                    SELECT record_id FROM downloads WHERE status = 'success'
                  )
                ORDER BY d.record_id
                LIMIT ?
                """,
                (args.max,),
            ).fetchall()
        ]

    print(f"Downloading for {len(record_ids)} records...")
    ok = failed = skipped = 0

    for record_id in record_ids:
        with connect(paths.db) as conn:
            meta = conn.execute(
                "SELECT canonical_id, oa_status FROM records WHERE id=?",
                (record_id,),
            ).fetchone()
            candidates = conn.execute(
                """
                SELECT id AS dl_id, url, file_format, resolver_source, status
                FROM downloads
                WHERE record_id=? AND status IN ('resolved', 'queued')
                ORDER BY CASE status WHEN 'resolved' THEN 0 ELSE 1 END, id
                """,
                (record_id,),
            ).fetchall()

        if not meta or not candidates:
            continue

        oa_status = (meta["oa_status"] or "unknown").lower()
        # arXiv/green candidates are always allowed; only skip closed records
        # when there is no candidate that carries an allowed tier via resolve time.
        if oa_status not in ALLOWED_OA_TIERS and oa_status not in ("unknown",):
            with connect(paths.db) as conn:
                for c in candidates:
                    conn.execute(
                        "UPDATE downloads SET status='skipped_closed', "
                        "error=? WHERE id=?",
                        (f"oa_status={oa_status} not in allowed tiers", c["dl_id"]),
                    )
                log_event(conn, "download", "info",
                          f"Skipped non-OA tier: {meta['canonical_id']}",
                          {"oa_status": oa_status})
            skipped += 1
            print(f"  SKIP {meta['canonical_id']} (oa_status={oa_status})")
            continue

        succeeded = False
        last_err = None
        for c in candidates:
            url = direct_download_url(c["url"])
            if url != c["url"]:
                # Candidates resolved before the rewrite existed.
                with connect(paths.db) as conn:
                    conn.execute("UPDATE downloads SET url=? WHERE id=?",
                                 (url, c["dl_id"]))
            try:
                if c["resolver_source"] in LANDING_SOURCES:
                    raise ValueError(LANDING_REASON)
                req = urllib.request.Request(
                    url, headers={"User-Agent": USER_AGENT}
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                check = check_fulltext(data, c["file_format"])
                if not check.ok:
                    raise ValueError(check.reason)
                fname = _safe_filename(meta["canonical_id"], check.detected_format)
                out_path = paths.fulltext / fname
                out_path.write_bytes(data)
                with connect(paths.db) as conn:
                    conn.execute(
                        "UPDATE downloads SET status='success', file_path=?, "
                        "file_format=?, error=NULL WHERE id=?",
                        (str(out_path.relative_to(paths.root)),
                         check.detected_format, c["dl_id"]),
                    )
                    # Cancel leftover candidates for this record
                    conn.execute(
                        """
                        UPDATE downloads SET status='skipped_superseded',
                               error='superseded by successful download'
                        WHERE record_id=? AND id!=? AND status IN ('resolved', 'queued')
                        """,
                        (record_id, c["dl_id"]),
                    )
                ok += 1
                print(
                    f"  OK {fname} via {c['resolver_source']} "
                    f"({len(data)//1024} KB)"
                )
                succeeded = True
                break
            except Exception as e:
                last_err = e
                with connect(paths.db) as conn:
                    conn.execute(
                        "UPDATE downloads SET status='failed', error=? WHERE id=?",
                        (str(e)[:500], c["dl_id"]),
                    )
                    log_event(conn, "download", "warn",
                              f"Failed: {meta['canonical_id']}",
                              {"url": url, "resolver_source": c["resolver_source"],
                               "error": str(e)})
                print(
                    f"  FAIL {meta['canonical_id']} via {c['resolver_source']}: {e}"
                )
            time.sleep(args.sleep)

        if not succeeded:
            failed += 1
            if last_err is None:
                print(f"  FAIL {meta['canonical_id']}: no candidates")

        if succeeded:
            time.sleep(args.sleep)

    print()
    print(f"Success: {ok}")
    print(f"Failed (all candidates):  {failed}")
    if skipped:
        print(f"Skipped (non-OA tier): {skipped}")

    # Refresh structured ILL report after the pass
    with connect(paths.db) as conn:
        nd_rows = rows_to_dicts(fetch_not_downloaded(conn))
    if nd_rows:
        csv_path, txt_path = write_not_downloaded_report(paths.screening, nd_rows)
        print(f"Not downloaded: {len(nd_rows)} — see {csv_path.name} / {txt_path.name}")

    print()
    print("Next: python scripts/07_fulltext_prep.py --project", args.project,
          "(or scripts/09_export.py if skipping full-text pass)")


if __name__ == "__main__":
    main()
