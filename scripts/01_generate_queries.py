#!/usr/bin/env python3
"""Generate query scaffolding for an SLR-Engine project.

This script produces TEMPLATE files in projects/<id>/queries/. The coding
agent is then expected to:
1. Read project.yaml's question, inclusion, exclusion, seeds
2. Replace the placeholders with real concepts and Boolean strings
3. Show the queries to the user for review

The script does NOT call an LLM. It only sets up the files. The agent fills
them in based on the screening criteria — that's deliberately a judgment task.

Usage:
  python scripts/01_generate_queries.py --project <project_id>
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slr_engine.store import ProjectConfig, ProjectPaths


CONCEPTS_TEMPLATE = """# Concepts extracted from the research question.
# Agent: replace placeholders with real concepts. One concept per block.
# Each concept needs preferred term + synonyms.

concepts:
  - id: C1
    preferred: "<concept 1>"
    synonyms:
      - "<synonym>"
      - "<synonym>"
  - id: C2
    preferred: "<concept 2>"
    synonyms:
      - "<synonym>"

# Boolean shape:
# (C1 terms) AND (C2 terms) AND ... AND (filters)
"""


OPENALEX_TEMPLATE = """\
# OpenAlex search string.
#
# REQUIRED structure:
#   ( <synonym1> OR <synonym2> OR ... )      <- concept group, OR'd
#   AND
#   ( <synonym1> OR <synonym2> OR ... )      <- another concept group
#   AND
#   ( ... )
#
# A flat list of terms with no Boolean operators will be interpreted as
# implicit AND across every term and likely return 0 hits. The query
# validator will block that case.
#
# Example shape (replace with your concept groups from the curated
# vocabulary in seeds/_vocabulary.json):
#   ("<concept A term 1>" OR "<concept A term 2>" OR "<concept A term 3>")
#   AND ("<concept B term 1>" OR "<concept B term 2>")
#   AND (empirical OR experiment* OR data)

<replace with Boolean string>
"""

PUBMED_TEMPLATE = """\
# PubMed E-utilities query. Uses MeSH and field tags.
# Example:
#   ("Large Language Models"[MeSH] OR "LLM"[tiab])
#   AND ("Code Review"[MeSH] OR "pull request"[tiab])

<replace with Boolean string>
"""

EUROPE_PMC_TEMPLATE = """\
# Europe PMC query. Uses field tags like TITLE_ABS, KW.
# Example: (TITLE_ABS:"code review" OR TITLE_ABS:"pull request") AND (TITLE_ABS:"LLM" OR TITLE_ABS:"large language model")

<replace with Boolean string>
"""

CROSSREF_TEMPLATE = """{
  "_comment_format": "Crossref structured query format. See https://api.crossref.org/swagger-ui/",
  "_comment_filter": "filter is STRICT (excludes non-matching). Common keys: type (journal-article, posted-content, book-chapter), has-abstract (true), container-title (specific journal), member (publisher id), from-pub-date, until-pub-date.",
  "_comment_query": "query.bibliographic does relevance ranking (NOT strict). Use only narrow concept groups with distinctive vocabulary. Repeated query.bibliographic values get AND-combined for ranking. query.title is even tighter (title only).",

  "filter": {
    "type": "journal-article",
    "has-abstract": "true"
  },

  "query.bibliographic": [
    "<narrow concept group A — named entities, distinctive terms>",
    "<narrow concept group B — distinctive vocabulary>"
  ]
}
"""

ARXIV_TEMPLATE = """\
# arXiv API query. Field tags: ti: (title), abs: (abstract), au: (author),
# cat: (category, e.g. q-fin.ST or cs.LG). Join with AND/OR/ANDNOT.
# Example shape:
#   (ti:"<concept A>" OR abs:"<concept A>" OR ti:<distinctive term>)
#   AND (abs:"<concept B>" OR abs:"<related term>")
# Useful categories for finance/quant topics: q-fin.*, stat.*, cs.LG
# Useful for ML methods reviews: cs.LG, cs.AI, stat.ML

<replace with arXiv Boolean>
"""

SEMANTIC_SCHOLAR_TEMPLATE = """\
# Semantic Scholar search string (/paper/search).
#
# Plain text only. S2 relevance search has no query syntax: AND/OR/NOT are
# matched as ordinary words, quotes and parentheses are ignored, and
# hyphenated terms match nothing (write "vibe coding", not "vibe-coding").
# The query validator blocks Boolean operators and hyphenated terms.
#
# Use a few distinctive words for the review's core concept, e.g.:
#   <core term> <second distinctive term>

<replace with plain-text query>
"""

DBLP_TEMPLATE = """\
# DBLP search string. CS-specific. Simple syntax; avoid rich Boolean.
# Use top-relevance keywords because DBLP relevance-ranks.
# Example: prediction market regime detection
#
# Best for: ICML, NeurIPS, ICLR, AAAI, ACL, SIGIR, CHI, etc. papers.

<replace with simple keyword string>
"""

IA_SCHOLAR_TEMPLATE = """\
# Internet Archive Scholar search string. Best for grey literature, older
# scanned material, technical reports, and proceedings with uneven metadata.
# Use a short distinctive keyword string.

<replace with simple keyword string>
"""

SCOPUS_TEMPLATE = """\
# Scopus query (paste into Scopus Document Search → Advanced).
# Use TITLE-ABS-KEY, AND, OR, NOT.
# Example:
#   TITLE-ABS-KEY(("code review" OR "pull request") AND ("LLM" OR "large language model"))
#   AND PUBYEAR > 2020

<replace with Scopus Boolean>
"""

WOS_TEMPLATE = """\
# Web of Science query (paste into WoS Advanced Search).
# Use TS= for topic search, AND/OR/NOT.
# Example:
#   TS=(("code review" OR "pull request") AND ("LLM" OR "large language model"))
#   AND PY=(2021-2025)

<replace with WoS Boolean>
"""

SCHOLAR_TEMPLATE = """\
# Google Scholar manual import.
#
# Google Scholar has no public API, so the engine does not scrape it.
# Manual flow:
#   1. Run the query in Google Scholar.
#   2. Save relevant results to My library or export citations per paper.
#   3. Export RIS/BibTeX if available.
#   4. Save files under projects/<id>/imports/ as scholar_<date>.ris.
#   5. Run: python scripts/02b_ingest_manual.py --project <id>

<replace with Google Scholar query>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument(
        "--projects-root",
        default=str(Path(__file__).resolve().parents[1] / "projects"),
    )
    args = ap.parse_args()

    project_dir = Path(args.projects_root) / args.project
    cfg = ProjectConfig.load(project_dir)
    paths = ProjectPaths(project_dir)
    paths.ensure()

    files = {
        "concepts.yaml":      CONCEPTS_TEMPLATE,
        "openalex.txt":       OPENALEX_TEMPLATE,
        "pubmed.txt":         PUBMED_TEMPLATE,
        "europepmc.txt":      EUROPE_PMC_TEMPLATE,
        "crossref.json":      CROSSREF_TEMPLATE,
        "arxiv.txt":          ARXIV_TEMPLATE,
        "semantic_scholar.txt": SEMANTIC_SCHOLAR_TEMPLATE,
        "dblp.txt":           DBLP_TEMPLATE,
        "ia_scholar.txt":     IA_SCHOLAR_TEMPLATE,
        "manual_scopus.txt":  SCOPUS_TEMPLATE,
        "manual_wos.txt":     WOS_TEMPLATE,
        "manual_scholar.txt": SCHOLAR_TEMPLATE,
    }

    created = []
    skipped = []
    for name, content in files.items():
        path = paths.queries / name
        if path.exists():
            skipped.append(name)
        else:
            path.write_text(content, encoding="utf-8")
            created.append(name)

    print(f"Project: {cfg.project_id}")
    print(f"Question: {cfg.question}")
    print()
    if created:
        print("Created query templates in", paths.queries)
        for n in created:
            print(f"  + {n}")
    if skipped:
        print("Skipped (already exist):")
        for n in skipped:
            print(f"  · {n}")
    print()
    print("Agent: now fill in the templates based on project.yaml.")
    print("After review, run: python scripts/02_search_open.py --project",
          args.project)


if __name__ == "__main__":
    main()
