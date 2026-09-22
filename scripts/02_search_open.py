#!/usr/bin/env python3
"""Run searches against open-access APIs and store results.

Reads queries from projects/<id>/queries/. For each enabled source, executes
the query, normalizes results, and stores them via slr_engine.store.insert_source_hit.

v0.5 additions:
  - Pre-flight query validation (slr_engine.query_validator). Errors block; warnings
    require --acknowledge-warnings or interactive override.
  - Silent-zero detection: when a source returns 0 records AND the adapter
    recorded HTTP errors, the message is surfaced as an error event, not
    "0 records ingested".
  - Post-flight sanity check: cap-hits, asymmetric coverage, total too
    high/low, zero-from-primary. Writes a flag to the events log; the
    next stage (03_dedup) checks for unaddressed warnings.

Idempotent: re-running won't duplicate source_hits (UNIQUE constraint).

Usage:
  python scripts/02_search_open.py --project <id>
  python scripts/02_search_open.py --project <id> --sources openalex,pubmed
  python scripts/02_search_open.py --project <id> --max-records 500
  python scripts/02_search_open.py --project <id> --acknowledge-warnings
"""
import argparse
import datetime as dt
import json
import sys
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slr_engine.env import (
    load_dotenv, openalex_api_key, pubmed_api_key, openalex_require_abstract,
)
from slr_engine.store import (
    ProjectConfig, ProjectPaths, connect, insert_source_hit, log_event,
    record_query, backfill_local_relevance_scores
)
from slr_engine.sources import get_adapter
from slr_engine.query_validator import (
    validate_all, render_summary, has_blocking_errors, has_warnings,
)
from slr_engine.sanity import check_post_search


load_dotenv()


SOURCE_QUERY_FILE = {
    "openalex":   "openalex.txt",
    "crossref":   "crossref.json",
    "pubmed":     "pubmed.txt",
    "europe_pmc": "europepmc.txt",
    "arxiv":      "arxiv.txt",
    "semantic_scholar": "semantic_scholar.txt",
    "dblp":       "dblp.txt",
    "ia_scholar": "ia_scholar.txt",
}


def _failure_note(errors: list[str], n: int, limit: int = 500) -> str:
    """Note stored with a query whose requests failed, so the methodology
    report does not present a failed search as a genuine count."""
    outcome = ("search failed; 0 is not a real null result"
               if n == 0 else "coverage may be incomplete")
    first = errors[0]
    if len(first) > limit:
        first = first[:limit].rsplit(" ", 1)[0] + " …"
    return f"{len(errors)} request error(s), {outcome}: {first}"


def _read_query(queries_dir: Path, source: str) -> tuple[Any, str]:
    """Return (query, query_for_audit_log).

    query is the value to pass to adapter.search() — string for most
    sources, dict for crossref's structured params.
    """
    path = queries_dir / SOURCE_QUERY_FILE[source]
    if not path.exists():
        return None, ""
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return None, ""

    if source == "crossref":
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            print(f"[error] {source}: malformed JSON in {path.name}: {e}")
            return None, ""

        # New v0.5 shape: structured params
        if any(k in obj for k in (
            "query.bibliographic", "query.title", "filter",
            "query.author", "query.container-title",
        )):
            audit = json.dumps(obj, ensure_ascii=False, sort_keys=True)
            return obj, audit

        # Legacy v0.4 shape: {"query": "...", "filters": {...}}
        legacy_query = obj.get("query", "")
        legacy_filters = obj.get("filter") or obj.get("filters")
        if legacy_filters and isinstance(legacy_filters, dict):
            structured = {
                "query.bibliographic": legacy_query,
                "filter": legacy_filters,
            }
            audit = json.dumps(structured, ensure_ascii=False, sort_keys=True)
            return structured, audit
        return legacy_query, legacy_query

    # Plain-text query files: strip comment lines
    lines = [
        l for l in text.splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]
    q = "\n".join(lines).strip()
    return q, q


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--sources",
                    help="Comma-separated subset, e.g. openalex,pubmed")
    ap.add_argument("--max-records", type=int, default=2000)
    ap.add_argument(
        "--acknowledge-warnings", action="store_true",
        help="Proceed past validator/sanity warnings without prompt.",
    )
    ap.add_argument(
        "--projects-root",
        default=str(Path(__file__).resolve().parents[1] / "projects"),
    )
    args = ap.parse_args()

    project_dir = Path(args.projects_root) / args.project
    cfg = ProjectConfig.load(project_dir)
    paths = ProjectPaths(project_dir)
    paths.ensure()

    # Determine enabled sources
    enabled = []
    for name in [
        "openalex", "crossref", "pubmed", "europe_pmc", "arxiv",
        "semantic_scholar", "dblp", "ia_scholar",
    ]:
        if cfg.sources.get(name, False) is True:
            enabled.append(name)
    if args.sources:
        requested = {s.strip() for s in args.sources.split(",")}
        enabled = [s for s in enabled if s in requested]

    if not enabled:
        print("No enabled API sources to run.")
        return

    # ---- Read queries ----
    queries_for_run: dict[str, Any] = {}
    audits: dict[str, str] = {}
    for source in enabled:
        q, audit = _read_query(paths.queries, source)
        if q is None or (isinstance(q, str) and not q.strip()):
            print(f"[skip] {source}: no query found in "
                  f"{SOURCE_QUERY_FILE[source]}")
            continue
        queries_for_run[source] = q
        audits[source] = audit

    if not queries_for_run:
        print("No queries to run.")
        return

    if "openalex" in queries_for_run and not openalex_api_key(cfg):
        print("[warn] OpenAlex: no OPENALEX_API_KEY set. Long Boolean queries")
        print("       may fail with HTTP 400. Set OPENALEX_API_KEY in .env or")
        print("       openalex_api_key in project.yaml.")

    # ---- Pre-flight validation (item 4) ----
    print("Validating queries...")
    results = validate_all(queries_for_run)
    print()
    print(render_summary(results))
    print()

    if has_blocking_errors(results):
        print("=" * 60)
        print("BLOCKED: queries above have structural errors.")
        print("Fix them and re-run. Search not executed.")
        print("=" * 60)
        # Log the validation failure to the audit trail
        with connect(paths.db) as conn:
            log_event(
                conn, "search", "error",
                "Pre-flight query validation failed; search aborted",
                {"validation_errors": [
                    {"source": r.source, "errors": r.errors}
                    for r in results if r.has_errors
                ]}
            )
        sys.exit(2)

    if has_warnings(results) and not args.acknowledge_warnings:
        print("=" * 60)
        print("Validator emitted warnings (above).")
        print("Pass --acknowledge-warnings to proceed.")
        print("=" * 60)
        sys.exit(2)

    # ---- Run searches ----
    log_path = paths.logs / "search.log"
    log_f = open(log_path, "a", encoding="utf-8")
    log_f.write(f"\n--- search run "
                f"{dt.datetime.now(dt.timezone.utc).isoformat()} ---\n")

    counts: dict[str, int] = {}
    silent_failures: list[str] = []  # sources that returned 0 with errors

    for source, query in queries_for_run.items():
        adapter = get_adapter(
            source,
            contact_email=cfg.contact_email,
            openalex_api_key=openalex_api_key(cfg),
            pubmed_api_key=pubmed_api_key(cfg),
            require_abstract=openalex_require_abstract(cfg),
        )
        adapter.reset_errors()
        query_id = (
            f"{source}_"
            f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        )
        audit_str = audits[source]
        n = 0
        print(f"[run] {source}: query_id={query_id}", flush=True)
        try:
            with connect(paths.db) as conn:
                for rec in adapter.search(
                    query,
                    date_from=cfg.date_from,
                    date_to=cfg.date_to,
                    languages=cfg.languages,
                    max_records=args.max_records,
                ):
                    insert_source_hit(
                        conn,
                        source=rec.source,
                        source_id=rec.source_id,
                        query_id=query_id,
                        raw=rec.raw or {},
                        title=rec.title,
                        abstract=rec.abstract,
                        authors=rec.authors,
                        year=rec.year,
                        doi=rec.doi,
                        pmid=rec.pmid,
                        pmcid=rec.pmcid,
                        openalex_id=rec.openalex_id,
                        venue=rec.venue,
                        document_type=rec.document_type,
                        language=rec.language,
                        keywords=rec.keywords,
                        url=rec.url,
                        tldr=rec.tldr,
                        snowball_rank=rec.snowball_rank,
                        native_relevance_score=rec.native_relevance_score,
                    )
                    n += 1
                    if n % 50 == 0:
                        print(f"  {source}: {n} records...", flush=True)

                # ---- Silent-zero detection (item 6) ----
                adapter_errors = adapter.errors_during_run
                note = _failure_note(adapter_errors, n) if adapter_errors else ""
                record_query(conn, query_id, source, audit_str, None, n, note)

                if n == 0 and adapter_errors:
                    silent_failures.append(source)
                    msg = (
                        f"{source}: 0 records ingested AFTER "
                        f"{len(adapter_errors)} request error(s) — "
                        f"this is likely a network or API failure, "
                        f"not a real null result"
                    )
                    print(f"  [silent-zero] {msg}")
                    log_event(
                        conn, "search", "error", msg,
                        {"query_id": query_id, "count": 0,
                         "adapter_errors": adapter_errors[:5]}
                    )
                elif adapter_errors:
                    # Got some records but errored along the way
                    msg = (
                        f"{source}: {n} records ingested with "
                        f"{len(adapter_errors)} request error(s) along the "
                        f"way — coverage may be incomplete"
                    )
                    print(f"  [partial] {msg}")
                    log_event(
                        conn, "search", "warning", msg,
                        {"query_id": query_id, "count": n,
                         "adapter_errors": adapter_errors[:5]}
                    )
                else:
                    log_event(
                        conn, "search", "info",
                        f"{source}: {n} records ingested",
                        {"query_id": query_id, "count": n}
                    )
        except Exception as e:
            err = f"[error] {source}: {e}\n{traceback.format_exc()}"
            print(err)
            log_f.write(err + "\n")
            with connect(paths.db) as conn:
                log_event(
                    conn, "search", "error", str(e),
                    {"source": source, "query_id": query_id}
                )
        else:
            log_f.write(f"[ok] {source}: {n} records\n")
            counts[source] = n
            print(f"[done] {source}: {n} records")

    log_f.close()

    # ---- Summary ----
    print()
    print("Search summary:")
    for s, n in counts.items():
        print(f"  {s}: {n}")
    print(f"  total: {sum(counts.values())}")
    print()

    # ---- Local relevance scoring (fills the gap for sources with no
    # native ranking score, and gives a cross-source-comparable signal) ----
    with connect(paths.db) as conn:
        rel_status = backfill_local_relevance_scores(conn, paths)
        if rel_status["scored"]:
            print(f"Scored {rel_status['scored']} records via local embedding "
                  f"relevance (vs. seed papers).")
            log_event(
                conn, "search", "info",
                f"Backfilled local relevance_score for {rel_status['scored']} records",
                {"scored": rel_status["scored"]}
            )
        elif rel_status["skipped_reason"]:
            print(f"[note] Skipped local relevance scoring: {rel_status['skipped_reason']}")
        print()

    # ---- Post-flight sanity (item 5) ----
    print("Post-search sanity check...")
    sanity = check_post_search(
        counts=counts,
        silent_failures=silent_failures,
        max_records_cap=args.max_records,
    )
    print()
    print(sanity.render())
    print()

    # Write sanity findings to audit log
    with connect(paths.db) as conn:
        if sanity.has_blocking:
            log_event(
                conn, "search", "error",
                "Post-search sanity check found blocking issues",
                {"issues": sanity.blocking + sanity.warnings}
            )
        elif sanity.has_warnings:
            log_event(
                conn, "search", "warning",
                "Post-search sanity check found warnings",
                {"warnings": sanity.warnings}
            )

    if sanity.has_blocking and not args.acknowledge_warnings:
        print("=" * 60)
        print("BLOCKED: post-search sanity check found issues that need "
              "acknowledgement before dedup.")
        print()
        print("Fix the search (rewrite queries, change source mix, etc.)")
        print("and re-run, OR pass --acknowledge-warnings to proceed despite")
        print("these issues.")
        print("=" * 60)
        sys.exit(3)

    if sanity.has_warnings and not args.acknowledge_warnings:
        print("Sanity warnings present. Pass --acknowledge-warnings to dedup,")
        print("or revise queries first if the warnings indicate a real issue.")

    print()
    print(f"Next: python scripts/02b_ingest_manual.py --project "
          f"{args.project} (if you have Scopus/WoS/Scholar exports)")
    print(f"Then:  python scripts/03_dedup.py --project {args.project}")


if __name__ == "__main__":
    main()
