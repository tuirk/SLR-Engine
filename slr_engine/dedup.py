"""Deduplication of records.

Stage 1 (in store.insert_source_hit): exact match on DOI, PMID, PMCID, OpenAlex ID.
Stage 2 (this module): fuzzy title + first_author + year, run after all sources
loaded.
"""
from __future__ import annotations

import json
import sqlite3
from difflib import SequenceMatcher

from .store import log_event


FUZZY_TITLE_THRESHOLD = 0.92


def _title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def fuzzy_dedup(conn: sqlite3.Connection) -> dict:
    """Find and merge near-duplicate records by (title_norm, first_author, year).

    Returns counts. Re-runs are safe — merges are idempotent because we always
    keep the lowest record id and reassign source_hits.
    """
    rows = conn.execute(
        "SELECT id, canonical_id, title_norm, first_author, year FROM records "
        "ORDER BY id"
    ).fetchall()

    # Bucket by (first_author_lower, year) to avoid O(n^2) across whole set.
    buckets: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (
            (r["first_author"] or "").strip().lower(),
            r["year"]
        )
        buckets.setdefault(key, []).append(dict(r))

    merges = 0
    examined = 0
    for key, items in buckets.items():
        if len(items) < 2:
            continue
        # Skip buckets where either part of the key is empty — too risky
        if not key[0] or not key[1]:
            continue
        # Naive O(k^2) within bucket. Buckets are small in practice.
        for i in range(len(items)):
            keep = items[i]
            if keep is None:
                continue
            for j in range(i + 1, len(items)):
                cand = items[j]
                if cand is None:
                    continue
                examined += 1
                sim = _title_similarity(keep["title_norm"], cand["title_norm"])
                if sim >= FUZZY_TITLE_THRESHOLD:
                    _merge(conn, keep_id=keep["id"], drop_id=cand["id"],
                           confidence=sim)
                    items[j] = None
                    merges += 1

    log_event(conn, "dedup", "info",
              f"Fuzzy dedup: examined {examined} pairs, merged {merges}",
              {"merges": merges, "examined": examined})
    return {"examined": examined, "merges": merges}


def _merge(conn: sqlite3.Connection, *, keep_id: int, drop_id: int,
           confidence: float) -> None:
    """Move all source_hits from drop_id to keep_id, then delete drop_id.
    Backfill any IDs the keep record lacks."""
    drop = conn.execute(
        "SELECT canonical_id, doi, pmid, pmcid, openalex_id, abstract, "
        "from_seed, tldr, snowball_rank "
        "FROM records WHERE id = ?", (drop_id,)
    ).fetchone()
    if drop is None:
        return

    conn.execute(
        "UPDATE records SET "
        "doi = COALESCE(doi, ?), pmid = COALESCE(pmid, ?), "
        "pmcid = COALESCE(pmcid, ?), openalex_id = COALESCE(openalex_id, ?), "
        "abstract = COALESCE(abstract, ?), "
        "tldr = COALESCE(tldr, ?), "
        "from_seed = CASE WHEN from_seed = 1 OR ? = 1 THEN 1 ELSE 0 END, "
        "snowball_rank = CASE "
        "WHEN snowball_rank IS NULL THEN ? "
        "WHEN ? IS NULL THEN snowball_rank "
        "WHEN ? > snowball_rank THEN ? "
        "ELSE snowball_rank END "
        "WHERE id = ?",
        (drop["doi"], drop["pmid"], drop["pmcid"], drop["openalex_id"],
         drop["abstract"], drop["tldr"], drop["from_seed"],
         drop["snowball_rank"], drop["snowball_rank"],
         drop["snowball_rank"], drop["snowball_rank"], keep_id)
    )

    hits = conn.execute(
        "SELECT id, source, source_id FROM source_hits WHERE record_id = ?",
        (drop_id,)
    ).fetchall()
    for h in hits:
        # (source, source_id) is UNIQUE, so the hit can always move. Keeping it
        # preserves provenance (e.g. the arXiv id stage 05 resolves PDFs from)
        # and the per-database counts PRISMA reports as identified.
        conn.execute(
            "UPDATE source_hits SET record_id = ? WHERE id = ?",
            (keep_id, h["id"])
        )
        conn.execute(
            "INSERT INTO dedup_log "
            "(canonical_id, merged_source, merged_source_id, "
            "match_method, match_confidence) "
            "SELECT canonical_id, ?, ?, 'fuzzy_title', ? FROM records WHERE id = ?",
            (h["source"], h["source_id"], confidence, keep_id)
        )

    _migrate_screening(conn, keep_id=keep_id, drop_id=drop_id)
    conn.execute("DELETE FROM records WHERE id = ?", (drop_id,))


def _migrate_screening(conn: sqlite3.Connection, *, keep_id: int, drop_id: int) -> None:
    """Move screening rows from drop_id to keep_id, resolving conflicts."""
    rows = conn.execute(
        "SELECT pass, decided_by, decision, reason, criteria_hit, decided_at, batch_id "
        "FROM screening WHERE record_id = ?",
        (drop_id,)
    ).fetchall()
    for row in rows:
        existing = conn.execute(
            "SELECT id FROM screening "
            "WHERE record_id = ? AND pass = ? AND decided_by = ?",
            (keep_id, row["pass"], row["decided_by"])
        ).fetchone()
        if existing:
            conn.execute("DELETE FROM screening WHERE record_id = ? AND pass = ? AND decided_by = ?",
                         (drop_id, row["pass"], row["decided_by"]))
        else:
            conn.execute(
                "UPDATE screening SET record_id = ? "
                "WHERE record_id = ? AND pass = ? AND decided_by = ?",
                (keep_id, drop_id, row["pass"], row["decided_by"])
            )
