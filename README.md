# SLR-Engine

SLR-Engine is an automated systematic literature review (SLR) pipeline with
human-in-the-loop where needed.

A systematic literature review (SLR) is a structured method to find, screen, and
summarize published research on a specific question using explicit search
strategies and documented inclusion criteria—not ad-hoc searching. Not sure how
that differs from asking an agent to search the web? See
[SLR-Engine vs agent deep / web search](#slr-engine-vs-agent-deep--web-search).

SLR-Engine combines **pipeline stages** (deterministic steps) with an **operator skill** so your coding agent runs and walks you through the workflow. For a plain-language tour of the conversation and pipeline, see the [introduction article](docs/articles/introduction-to-slr-engine.md). For why the project exists and how it is designed, see [Why I built SLR-Engine](docs/articles/why-i-built-slr-engine.md).

## Contents

- [What will you get if you use SLR-Engine](#what-will-you-get-if-you-use-slr-engine)
- [SLR-Engine vs agent deep / web search](#slr-engine-vs-agent-deep--web-search)
- [Quick start](#quick-start)
- [How SLR-Engine is built](#how-slr-engine-is-built)
- [Step-by-step workflow](#step-by-step-workflow)
- [Supported sources](#supported-sources)
- [Optional dependencies](#optional-dependencies)
- [License](#license)

---

## What will you get if you use SLR-Engine

You get a **project folder** on disk under `projects/<id>/`: screened papers plus **research reporting** you can show in a thesis, report, or methods appendix—not just a chat summary. Stage-by-stage file layout and time estimates are in the [review process walkthrough](docs/articles/review-process-walkthrough.md).

**Evidence set**

- **Downloaded open-access PDFs** (when found) — full papers in the project folder, not just abstracts.
- **Your shortlist** — which papers made the cut, with include/exclude reasons.
- **Spreadsheet of every paper touched** — title, source, decisions, who decided (`records.csv` / `records.jsonl`).
- **Import file for reference tools** — Zotero, Mendeley, etc. (`included.ris`).
- **Optional study notes** — fields pulled from full-text reading, plus optional quality / risk-of-bias ratings when you run that pass (`extractions.csv`).

**Reporting and traceability**

- **PRISMA flow diagrams** — standard and expanded charts of how many records were identified, screened, included, and excluded. Methodology background: [Methodological foundations](docs/articles/methodological-foundations.md).
- **Methods report** — a readable write-up of your search, screening, and decisions (`methodology_report.md`).
- **Full audit log** — exact queries run, duplicates merged, and screening counts (`audit.json`).
- **Optional protocol draft** — a prospective plan before search, if you generate one at scoping (`protocol_draft.md`).

You can stop mid-review and resume; the folder keeps queries, screening work, and downloads until export. Ready for synthesis, writing, or analysis.

---

## SLR-Engine vs agent deep / web search

**Agent deep search and web skills** answer a question in chat: search the web, read pages, summarize, cite a few links. Fast and conversational—good for a quick take.

**SLR-Engine** runs a structured review on disk. The output is a reproducible dataset of screened academic papers with decision history, not a conversational answer. See [What will you get](#what-will-you-get-if-you-use-slr-engine) above for the on-disk deliverables.

Use deep search when you need a quick read. Use SLR-Engine when the deliverable is a traceable paper set you can export, revisit, and defend.

**Good fit:** students, researchers, hobbyists, analysts, knowledge workers—anyone who needs real sources to ground their work on.

| | Agent deep / web search | SLR-Engine |
|---|-------------------------|--------|
| **Output** | Summary + ad-hoc links | Shortlist + CSV/RIS + audit log |
| **Sources** | Web, blogs, news, mixed quality | Academic APIs ([supported sources](#supported-sources)) |
| **Curation** | Model picks what looks relevant | You set include/exclude; screen in batches |
| **Dedup** | Same paper may appear from different URLs | Cross-source dedup by DOI / title / author |
| **Reproducibility** | Hard to replay what was searched | Saved queries, counts, and decisions in `projects/` |
| **Resume** | New chat often means starting over | `Continue project [id]` |
| **Citation follow-up** | Rarely systematic | Snowball references and citations (stage **08**) |
| **Full text** | Snippets from pages fetched | OA resolve and download pipeline |
| **Citation accuracy** | Risk of invented or wrong links | Records from APIs and metadata, not free-form generation |
| **Speed** | Faster for orientation | Slower — by design |

---

## Quick start

Verify install: `python scripts/smoke_verify.py` (uses `projects/_demo/`).

1. Install the **operator skill** — copy [`skills/slr-engine/`](skills/slr-engine/) into your agent's skills folder (see [`AGENT.md`](AGENT.md#operator-skill-install)).
2. Open this repo in the agent and say: *"Help me start a literature review on [topic]."*
3. The agent scopes, searches, screens, and exports to `projects/<id>/exports/`. Resume: *"Continue project [id]."*

Install the skill if you can — without it, agents often explain the workflow instead of running it.

New to the project? Read the [introduction article](docs/articles/introduction-to-slr-engine.md) first. For every stage in detail (API keys, batches, files on disk), use the [review process walkthrough](docs/articles/review-process-walkthrough.md).

---

## How SLR-Engine is built

The **operator skill** ([`skills/slr-engine/SKILL.md`](skills/slr-engine/SKILL.md)) tells the agent what to do. **Pipeline stages** in `scripts/` do the work. The **core library** in `slr_engine/` is the implementation behind those stages. Each **review project** lives under `projects/<id>/`.

SLR-Engine expects a **coding agent** to run the workflow with you. Coding agents should open [`AGENT.md`](AGENT.md) when this workspace loads.

- **Pipeline stages** do the mechanical work: search databases, dedupe, resolve downloads, export files.
- **You + the agent** do the judgment work: scope the question, set inclusion rules, label screening batches (usually a few papers at a time in simple files on disk), and override anything that looks wrong.

In the default setup the core library never calls an LLM by itself; it prepares files, your agent reads them and runs the next pipeline stage. Optional API-based LLM screening exists for power users — see [Supported sources](#supported-sources).

Layer diagram and design principles: [Why I built SLR-Engine](docs/articles/why-i-built-slr-engine.md).

### Documentation

Human articles live in [`docs/articles/`](docs/articles/) on GitHub: [github.com/tuirk/SLR-Engine/tree/main/docs/articles](https://github.com/tuirk/SLR-Engine/tree/main/docs/articles).

| Path | Audience | What it covers |
|------|----------|----------------|
| [`README.md`](README.md) | Everyone | This file: overview, workflow table, quick start |
| [`docs/articles/why-i-built-slr-engine.md`](docs/articles/why-i-built-slr-engine.md) | Humans | Motivation, design principles, architecture |
| [`docs/articles/introduction-to-slr-engine.md`](docs/articles/introduction-to-slr-engine.md) | Humans | Plain-language introduction and conversation flow |
| [`docs/articles/review-process-walkthrough.md`](docs/articles/review-process-walkthrough.md) | Humans | Stage-by-stage walkthrough (files, keys, time) |
| [`docs/articles/methodological-foundations.md`](docs/articles/methodological-foundations.md) | Humans | Methodology pillars and references |
| [`AGENT.md`](AGENT.md) | Coding agents | Entry when the workspace opens |
| [`skills/slr-engine/SKILL.md`](skills/slr-engine/SKILL.md) | Coding agents | Operator skill: run and walk through a review |

---

## Step-by-step workflow

Pipeline stages live in `scripts/` (`00`–`09`). The table below is a compact reference; the [review process walkthrough](docs/articles/review-process-walkthrough.md) has the full narrative (who does what, how long it takes, what lands on disk).

The last column marks who runs each step: **script** (Python only), **agent** (coding agent via [`skills/slr-engine/SKILL.md`](skills/slr-engine/SKILL.md)), **user** (you), or a combination. Optional LLM stages (`04c`, `07c`, `08b`) add script-driven API calls where noted.

| # | Stage | What it does | Who |
|---|-------|--------------|-----|
| 00 | init | Create `projects/<id>/`: `project.yaml`, SQLite `project.db`, folders for seeds, queries, imports, screening, full text, logs, exports | — |
| 00b | read seeds | Ingest 1–3 anchor papers (DOI, OpenAlex ID, or PDF) as `from_seed` records; auto-included on first title/abstract commit. Start of **pearl growing** when you have good papers but weak Boolean queries | script |
| 00c | extract vocab | KeyBERT (optional) + agent curation → `seeds/_vocabulary.json`; canonical terms for queries and PICOC—avoid inventing search vocabulary from general knowledge | script + agent |
| 01 | query gen | Scaffold `queries/` templates; agent fills literals from `project.yaml` + vocabulary; you approve strings before **02**. **Keyword search** path after **00c** | agent |
| 01a | protocol draft (optional) | Emit prospective `protocol_draft.md` before search | agent + user |
| 02 | search (open) | Run approved queries on enabled APIs (OpenAlex, Crossref, arXiv, Semantic Scholar default; PubMed, Europe PMC, DBLP, IA Scholar optional). Pre-flight query validation; post-search sanity (silent zeros, cap hits). Records + `source_hits` + frozen `queries` in DB; `logs/search.log`. Sources with a native ranking score (OpenAlex, Crossref) stop paginating past a relevance floor, since neither supports true boolean/phrase queries — Crossref in particular is pure bag-of-words relevance ranking with no minimum score, quotes and `OR` are inert. A free local sentence-embedding score (`records.relevance_score`, vs. seed papers, comparable across every source) is computed automatically at the end of the run | script |
| 02b | ingest manual (optional) | Scopus / WoS / Google Scholar RIS or CSV from `imports/`—use partial paid access alongside free APIs | script |
| 03 | dedup | DOI / PMID / OpenAlex exact match, then fuzzy title+author+year; `dedup_log`. Blocks on unacknowledged search issues; blocks re-dedup after screening unless `--force` | script |
| 04 | screen prep | Export unscreened records to `screening/batch_*.jsonl` (≤5 per batch) + `criteria.md`. `--sort relevance_asc\|relevance_desc` orders batches by `relevance_score` (triage aid — every record still gets a real decision, nothing is auto-filtered) | script |
| 04b | T/A screen commit | Label batches (`decision`, `reason`, `criteria_hit`); commit with provenance (`agent`, `human`, `seed`, …) | agent |
| 04c | T/A screen LLM (optional) | Unattended LLM or agent handoff packets (`*_prompts.jsonl`) | script / agent |
| 05 | resolve OA | PMC → Europe PMC → OpenAlex → Unpaywall → CORE (optional) → Crossref; open-access copies only (closed works skipped) | script |
| 06 | download | Fetch OA full text to `data/fulltext/` | script |
| 07 | full-text prep | PDF/HTML/XML → `data/fulltext_md/`; intro/conclusion excerpts for triage; `not_downloaded.csv`/`.txt` for includes without a downloaded full text, with a suggested next step | script |
| 07b | full-text commit | Commit hand labels on full-text batches | agent |
| 07c | LLM full-text + extract (optional) | Screen + structured extraction on paper text; `--with-quality` adds PRISMA-oriented risk-of-bias fields | script + agent |
| 07d | human review | Review LLM recommendations; user overrides; final commit | user (final), agent (assistant) |
| 08 | snowball (optional) | Backward references + forward citations (OpenAlex; S2 ranks edges if enabled). **Pearl growing expand:** loop **08 → 03 → 04** (then **05–07** for new includes) until no new candidates | script |
| 08b | risk-of-bias (optional) | Post-hoc RoB on included papers | script + agent |
| 08c | quality commit | Commit RoB batch labels | agent |
| 09 | export | `records.csv` / `records.jsonl`, `included.ris`, `extractions.csv` (if any), `audit.json`, `methodology_report.md`, `prisma_flow.svg`, `expanded_prisma.svg`. Blocks if included lack RoB unless `--allow-missing-risk-of-bias` | script |

**Discovery paths (often combined):** *Pearl growing* — **00b** → **00c** → **04** → **08**, then loop **08 → 03 → 04**. *Keyword search* — **00b** → **00c** → **01** → **02** → **03** → **04**. Then shared path: **05** → **06** → **07** (optional **08b**) → **09**.

Example config: [`projects/_example/project.yaml`](projects/_example/project.yaml). Screening rules: [`skills/slr-engine/SKILL_screening.md`](skills/slr-engine/SKILL_screening.md).

---

## Supported sources

Three intake paths — all merge into the same dedup and screening pipeline. API keys, toggles, and rate limits are documented in the [walkthrough](docs/articles/review-process-walkthrough.md#before-anything-starts) (*Before anything starts*).

| Stage | Sources |
|-------|---------|
| **02 — open APIs** | OpenAlex · Crossref · arXiv · Semantic Scholar |
| **02 — optional** | PubMed · Europe PMC (clinical) · DBLP (CS) · IA Scholar (grey lit) — toggle in `project.yaml` |
| **02b — manual** | Scopus · Web of Science · Google Scholar → RIS/CSV in `imports/` as `scopus_*`, `wos_*`, `scholar_*` |
| **05–06 — full text** | PMC → Europe PMC → OpenAlex → Unpaywall → CORE (if `sources.core: true` + `CORE_API_KEY`) → Crossref |

**Optional keys and sources.** Nothing above requires paid accounts. Extra sources and `.env` keys (`OPENALEX_API_KEY`, `S2_API_KEY`, `NCBI_API_KEY`, `CORE_API_KEY`) are optional — add them for rate limits or extra resolvers. `CORE_API_KEY` is used only when `sources.core: true` in `project.yaml`. `OPENALEX_API_KEY` can also live in `project.yaml` as `openalex_api_key`. Set `contact_email` in `project.yaml`. Paywalled or hybrid tiers are not auto-downloaded; see `screening/not_downloaded.csv` / `not_downloaded.txt`. Resolve collects multiple OA candidates and download retries them in order.

**Default: agent drives judgment.** With no `llm:` block (or `provider: agent`), your coding agent handles vocabulary curation, screening, and full-text review via the [operator skill](skills/slr-engine/SKILL.md). The scripts handle search, dedup, resolve, download, and export.

**Optional: scripts call APIs directly.** Set `llm.provider` in `project.yaml` plus provider keys in `.env` to run unattended LLM stages (04c, 07c, 08b). That path and `agent_handoff_runner.py` are **stub/reference implementations** — workable, but the intended workflow is agent + skill, not headless automation.

---

## Optional dependencies

Core: stdlib + PyYAML (`pip install -r requirements.txt`). Everything below is optional.

| Add | Enables | Without it |
|-----|---------|------------|
| `keybert`, `sentence-transformers` | Better vocabulary at **00c** | Frequency fallback (weaker); agent still curates in agent mode |
| `markitdown`, PDF libs | PDF/HTML → markdown at **07** | Stage **07** fails on PDF conversion until installed (`requirements.txt` comments) |
| `llm:` + provider keys in `.env` | Unattended LLM at **04c** / **07c** / **08b** | Agent labels batches via skill (default) |

Direct LLM calls and `agent_handoff_runner.py` are stub/reference paths — see [Supported sources](#supported-sources).

---

## License

MIT — see [LICENSE](LICENSE). [Code of conduct](CODE_OF_CONDUCT.md).
