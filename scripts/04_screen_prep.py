#!/usr/bin/env python3
"""Prepare a screening batch as JSONL.

Selects unscreened records and writes them to projects/<id>/screening/batch_NNN.jsonl.
The agent reads the batch, fills in {decision, reason, criteria_hit} per line,
then runs 04b_screen_commit.py.

Usage:
  python scripts/04_screen_prep.py --project <id>
  python scripts/04_screen_prep.py --project <id> --batch-size 5
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slr_engine.store import ProjectConfig, ProjectPaths, connect


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--batch-size", type=int, default=5)
    ap.add_argument("--pass-name", default="title_abstract")
    ap.add_argument(
        "--sort",
        choices=["default", "relevance_asc", "relevance_desc"],
        default="default",
        help=(
            "default: snowball_rank-based ordering (existing behavior). "
            "relevance_asc: weakest local-embedding relevance_score first "
            "(useful for bulk-rejecting the long tail quickly). "
            "relevance_desc: strongest matches first. Records with no "
            "relevance_score sort last either way. Triage aid only -- "
            "every record still gets a real screening decision."
        ),
    )
    ap.add_argument(
        "--projects-root",
        default=str(Path(__file__).resolve().parents[1] / "projects"),
    )
    args = ap.parse_args()
    if args.batch_size > 5:
        print("Batch sizes above 5 are not allowed.")
        print("Run screening sequentially in batches of 5 or fewer.")
        sys.exit(1)

    project_dir = Path(args.projects_root) / args.project
    cfg = ProjectConfig.load(project_dir)
    paths = ProjectPaths(project_dir)
    paths.ensure()

    order_by = {
        "default": "(r.snowball_rank IS NULL), r.snowball_rank DESC, r.id",
        "relevance_asc": "(r.relevance_score IS NULL), r.relevance_score ASC, r.id",
        "relevance_desc": "(r.relevance_score IS NULL), r.relevance_score DESC, r.id",
    }[args.sort]

    with connect(paths.db) as conn:
        seed_clause = "AND COALESCE(r.from_seed, 0) = 0 " if args.pass_name == "title_abstract" else ""
        rows = conn.execute(
            "SELECT r.id, r.canonical_id, r.title, r.tldr, r.abstract, r.year, "
            "r.first_author, r.venue, r.doi, r.snowball_rank, r.relevance_score "
            "FROM records r "
            "LEFT JOIN screening s ON s.record_id = r.id AND s.pass = ? AND s.decided_by = 'agent' "
            "WHERE s.id IS NULL "
            f"{seed_clause}"
            f"ORDER BY {order_by} LIMIT ?",
            (args.pass_name, args.batch_size)
        ).fetchall()

    if not rows:
        print("Nothing to screen — all records have an agent decision for pass:",
              args.pass_name)
        return

    # Pick the next batch number from the highest existing batch file.
    existing = sorted(paths.screening.glob("batch_*.jsonl"))
    nums = []
    for p in existing:
        try:
            nums.append(int(p.stem.split("_")[1]))
        except (IndexError, ValueError):
            continue
    next_n = (max(nums) + 1) if nums else 1
    batch_path = paths.screening / f"batch_{next_n:03d}.jsonl"
    batch_id = batch_path.stem

    # Write criteria header to a sidecar so the agent can re-read mid-batch
    crit_path = paths.screening / "_criteria.md"
    # Always refresh criteria so mid-batch edits to project.yaml are visible.
    with open(crit_path, "w", encoding="utf-8") as f:
            f.write(f"# Criteria for {cfg.project_id}\n\n")
            f.write(f"**Question:** {cfg.question}\n\n")
            f.write("## Inclusion\n")
            for c in cfg.inclusion:
                f.write(f"- **{c.get('id', '?')}**: {c.get('text', '')}\n")
            f.write("\n## Exclusion\n")
            for c in cfg.exclusion:
                f.write(f"- **{c.get('id', '?')}**: {c.get('text', '')}\n")
            f.write("\n## Seed examples\n\n")
            f.write("### Should INCLUDE:\n")
            for s in cfg.seed_examples.get("include", []):
                f.write(f"- {s}\n")
            f.write("\n### Should EXCLUDE:\n")
            for s in cfg.seed_examples.get("exclude", []):
                f.write(f"- {s}\n")

    with open(batch_path, "w", encoding="utf-8") as f:
        for r in rows:
            obj = {
                "record_id": r["id"],
                "canonical_id": r["canonical_id"],
                "title": r["title"],
                "tldr": r["tldr"],
                "abstract": r["abstract"],
                "year": r["year"],
                "first_author": r["first_author"],
                "venue": r["venue"],
                "doi": r["doi"],
                "snowball_rank": r["snowball_rank"],
                "relevance_score": r["relevance_score"],
                "batch_id": batch_id,
                # To be filled in by the agent:
                "decision": None,
                "reason": None,
                "criteria_hit": [],
            }
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    print(f"Batch written: {batch_path}")
    print(f"Records:       {len(rows)}")
    print(f"Criteria:      {crit_path}")
    print()
    print("Agent: read skills/slr-engine/SKILL_screening.md and the criteria file, then label each line.")
    print(f"Commit with: python scripts/04b_screen_commit.py --batch {batch_path}")


if __name__ == "__main__":
    main()
