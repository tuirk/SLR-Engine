#!/usr/bin/env python3
"""Run fuzzy dedup over all records.

ID-based dedup (DOI, PMID, OpenAlex, PMCID) happens at insert time. This
script does the second pass: fuzzy title + first author + year.

v0.5: refuses to run if the most recent search stage emitted blocking
sanity issues (silent zeros, zero from primary, etc.) that haven't been
acknowledged. Pass --acknowledge-warnings to override.

Also refuses to run after title/abstract screening unless --force is passed,
because merges can invalidate screening decisions.

Usage:
  python scripts/03_dedup.py --project <id>
  python scripts/03_dedup.py --project <id> --acknowledge-warnings
  python scripts/03_dedup.py --project <id> --force
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slr_engine.store import ProjectPaths, connect
from slr_engine.dedup import fuzzy_dedup


def _last_search_blocking(conn) -> list[dict]:
    """Blocking search problems that are still current.

    Only each source's most recent query counts, so a source that failed and
    was then searched again successfully no longer blocks. A pre-flight
    validation failure blocks while no search has run since it.
    """
    latest: dict = {}
    for q in conn.execute(
        "SELECT query_id, source, result_count, notes, executed_at "
        "FROM queries ORDER BY executed_at, rowid"
    ).fetchall():
        latest[q["source"]] = q

    errored_queries = set()
    for e in conn.execute(
        "SELECT payload_json FROM events WHERE stage = 'search' AND level = 'error'"
    ).fetchall():
        try:
            query_id = json.loads(e["payload_json"] or "{}").get("query_id")
        except ValueError:
            continue
        if query_id:
            errored_queries.add(query_id)

    blocking = []
    for source, q in latest.items():
        if q["result_count"]:
            continue
        if q["notes"] or q["query_id"] in errored_queries:
            blocking.append({
                "message": f"{source}: latest search returned 0 records after "
                           "request errors (not a real null result)",
                "payload": q["notes"],
            })
        elif source == "openalex":
            blocking.append({
                "message": "openalex: latest search returned 0 records; the "
                           "query is likely malformed or far too narrow",
                "payload": None,
            })

    last_run = max((q["executed_at"] for q in latest.values()), default=None)
    for e in conn.execute(
        "SELECT message, payload_json, occurred_at FROM events "
        "WHERE stage = 'search' AND level = 'error' "
        "AND message LIKE 'Pre-flight query validation failed%' ORDER BY id"
    ).fetchall():
        if last_run is None or e["occurred_at"] > last_run:
            blocking.append({"message": e["message"], "payload": e["payload_json"]})
    return blocking


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument(
        "--acknowledge-warnings", action="store_true",
        help="Proceed even if the search stage reported blocking sanity issues.",
    )
    ap.add_argument(
        "--force", action="store_true",
        help="Re-run dedup after screening (may invalidate screening decisions).",
    )
    ap.add_argument(
        "--projects-root",
        default=str(Path(__file__).resolve().parents[1] / "projects"),
    )
    args = ap.parse_args()

    paths = ProjectPaths(Path(args.projects_root) / args.project)

    with connect(paths.db) as conn:
        # Precondition: did the search stage flag blocking issues?
        blocking = _last_search_blocking(conn)
        if blocking and not args.acknowledge_warnings:
            print("=" * 60)
            print("BLOCKED: the search stage reported issues that should be")
            print("addressed before dedup. Recent events:")
            print("=" * 60)
            for b in blocking[:5]:
                print(f"  - {b['message']}")
            print()
            print("Either fix the search (rewrite queries, change source mix,")
            print("etc.) and re-run scripts/02_search_open.py, OR pass")
            print("--acknowledge-warnings to dedup despite these issues.")
            sys.exit(2)

        screened = conn.execute(
            "SELECT COUNT(*) AS n FROM screening WHERE pass = 'title_abstract'"
        ).fetchone()["n"]
        if screened and not args.force:
            print("=" * 60)
            print("BLOCKED: title/abstract screening has already run.")
            print(f"  {screened} screening row(s) exist.")
            print()
            print("Re-running dedup can merge records and invalidate those")
            print("decisions. Pass --force if you intend to re-dedup anyway.")
            print("=" * 60)
            sys.exit(2)

        before = conn.execute(
            "SELECT COUNT(*) AS n FROM records"
        ).fetchone()["n"]
        result = fuzzy_dedup(conn)
        after = conn.execute(
            "SELECT COUNT(*) AS n FROM records"
        ).fetchone()["n"]
        hits = conn.execute(
            "SELECT COUNT(*) AS n FROM source_hits"
        ).fetchone()["n"]

    print(f"Records before: {before}")
    print(f"Records after:  {after}")
    print(f"Fuzzy merges:   {result['merges']} (examined "
          f"{result['examined']} pairs)")
    print(f"Source hits:    {hits}")
    print()
    print("Next: python scripts/04_screen_prep.py --project", args.project)


if __name__ == "__main__":
    main()
