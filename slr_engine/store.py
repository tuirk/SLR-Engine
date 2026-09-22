"""Project lifecycle and SQLite store."""
from __future__ import annotations

import json
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterator, Optional

import yaml

from .schema import SCHEMA_SQL


# ---------- project config ----------

@dataclass
class ProjectConfig:
    project_id: str
    # v0.4: layered statement of what the review is about.
    # `topic` = subject area (replaces old `question` field; `question` aliases to `topic`)
    # `aim` = what the review should achieve (optional, narrows topic)
    # `research_questions` = list of plain-English questions the review answers
    topic: str = ""
    aim: Optional[str] = None
    research_questions: list[str] = field(default_factory=list)
    # Backward-compat: old projects had `question`. We accept it on load and
    # mirror to `topic`. Save always writes the new fields.
    question: Optional[str] = None    # deprecated; load() migrates to topic
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    languages: list[str] = field(default_factory=lambda: ["en"])
    inclusion: list[dict] = field(default_factory=list)
    exclusion: list[dict] = field(default_factory=list)
    seeds: dict = field(default_factory=lambda: {"papers": [], "links": []})
    seed_examples: dict = field(default_factory=lambda: {"include": [], "exclude": []})
    # v0.4: framework for structuring the research question(s).
    # type: "picoc" | "custom" | None
    # slots: {slot_name: filling_string}
    # For PICOC, slot_names should be: population, intervention, comparison, outcome, context.
    # For custom, freeform — the user (or agent) describes their framework's slots.
    framework: Optional[dict] = None
    # v0.4: optional hypotheses. Soft cap at 3 — if more, an event is logged
    # in the audit trail and methodology artifacts add a hallucination-risk caveat.
    # Shape: [{id: "H1", statement: "...", rationale: "..."}]
    hypotheses: list[dict] = field(default_factory=list)
    sources: dict = field(default_factory=lambda: {
        "openalex": True, "crossref": True, "pubmed": False,
        "europe_pmc": False, "arxiv": True, "semantic_scholar": True,
        "dblp": False, "ia_scholar": False,
        # Stage-05 OA resolver toggle (requires CORE_API_KEY); not a search API.
        "core": False,
        "scopus": "manual", "web_of_science": "manual",
    })
    contact_email: Optional[str] = None
    openalex_api_key: Optional[str] = None
    pubmed_api_key: Optional[str] = None
    # Optional model config for unattended API-backed calls. Leave None to use
    # the coding agent / external harness as the judgment layer. Shape:
    # {"provider": "anthropic"|"deepseek"|"google"|"agent",
    #  "model": "claude-sonnet-4-5", "temperature": 0.0}
    llm: Optional[dict] = None
    # Optional extraction schema config. None → use the generic preset.
    # See slr_engine.llm.get_extraction_fields for the shape.
    extraction: Optional[dict] = None

    @classmethod
    def load(cls, project_dir: Path) -> "ProjectConfig":
        path = project_dir / "project.yaml"
        if not path.exists():
            raise FileNotFoundError(f"No project.yaml in {project_dir}")
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        # Tolerate unknown keys for forward-compat
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in raw.items() if k in known}
        cfg = cls(**filtered)
        # v0.4 migration: old projects had `question` only. Mirror to `topic`
        # if topic is empty. The `question` field stays as alias.
        if cfg.question and not cfg.topic:
            cfg.topic = cfg.question
        elif cfg.topic and not cfg.question:
            cfg.question = cfg.topic
        return cfg

    def save(self, project_dir: Path) -> None:
        # Keep both topic and question populated for backward-compat
        if self.topic and not self.question:
            self.question = self.topic
        with open(project_dir / "project.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(asdict(self), f, sort_keys=False)

    def review_question_for_llm(self) -> str:
        """Return the best representation of 'what the review is about' for
        feeding into LLM prompts. Prefer research_questions (specific, sharp)
        over topic (broad). Fall back to topic if no RQs are set."""
        if self.research_questions:
            return "\n".join(
                f"RQ{i+1}: {q}" for i, q in enumerate(self.research_questions)
            )
        return self.topic or self.question or ""

    def uses_agent_judgment(self) -> bool:
        """True when no API-backed provider is configured and an external
        coding agent / harness is expected to perform judgment steps."""
        if not self.llm:
            return True
        provider = str(self.llm.get("provider") or "").strip().lower()
        return provider in {"", "agent"}

    def judgment_provider(self) -> str:
        if self.uses_agent_judgment():
            return "agent"
        return str(self.llm.get("provider") or "agent")

    def judgment_model(self) -> str:
        if not self.llm:
            return "external-agent"
        return str(self.llm.get("model") or "external-agent")

    def hypothesis_cap_status(self) -> dict:
        """Return info about hypothesis count vs the soft cap of 3.

        Used by the agent/scoping conversation to surface a warning, and by
        methodology artifacts to decide whether to include the elevated-risk caveat.
        """
        n = len(self.hypotheses or [])
        cap = 3
        return {
            "count": n,
            "cap": cap,
            "exceeds_cap": n > cap,
            "risk_level": (
                "none" if n == 0
                else "low" if n == 1
                else "moderate" if n <= cap
                else "elevated"
            ),
        }


# ---------- project paths ----------

@dataclass
class ProjectPaths:
    root: Path

    @property
    def db(self) -> Path: return self.root / "project.db"
    @property
    def queries(self) -> Path: return self.root / "queries"
    @property
    def imports(self) -> Path: return self.root / "imports"
    @property
    def screening(self) -> Path: return self.root / "screening"
    @property
    def fulltext(self) -> Path: return self.root / "data" / "fulltext"
    @property
    def fulltext_md(self) -> Path: return self.root / "data" / "fulltext_md"
    @property
    def exports(self) -> Path: return self.root / "exports"
    @property
    def logs(self) -> Path: return self.root / "logs"

    def ensure(self) -> None:
        for p in [self.queries, self.imports, self.screening,
                  self.fulltext, self.fulltext_md, self.exports, self.logs]:
            p.mkdir(parents=True, exist_ok=True)


def init_project(projects_root: Path, project_id: str,
                 topic: str = "", question: Optional[str] = None) -> Path:
    """Create a new project folder with default config and DB.

    Accepts either `topic` (v0.4 preferred) or `question` (legacy).
    If both given, topic wins.
    """
    project_dir = projects_root / project_id
    paths = ProjectPaths(project_dir)
    if project_dir.exists():
        # Support bootstrapping tracked demo/template folders that exist
        # in git but do not yet have a local project.db.
        if paths.db.exists():
            raise FileExistsError(f"Project already exists: {project_dir}")
    paths.ensure()
    cfg = ProjectConfig(
        project_id=project_id,
        topic=topic or question or "",
    )
    cfg_path = project_dir / "project.yaml"
    if not cfg_path.exists():
        cfg.save(project_dir)
    if not paths.db.exists():
        init_db(paths.db)
    return project_dir


# ---------- store ----------

def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)


# Columns added after a project's db was first created. CREATE TABLE IF NOT
# EXISTS in SCHEMA_SQL only covers brand-new databases, so pre-existing
# project.db files need these backfilled on connect.
_RECORDS_COLUMN_MIGRATIONS = {
    "native_relevance_score": "REAL",
    "relevance_score": "REAL",
}


def _migrate_schema(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(records)")}
    for col, sql_type in _RECORDS_COLUMN_MIGRATIONS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE records ADD COLUMN {col} {sql_type}")


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate_schema(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- normalization helpers ----------

def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, strip diacritics."""
    if not title:
        return ""
    s = unicodedata.normalize("NFKD", title)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = "".join(c if c.isalnum() or c.isspace() else " " for c in s)
    s = " ".join(s.split())
    return s


def normalize_doi(doi: Optional[str]) -> Optional[str]:
    if not doi:
        return None
    doi = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi or None


def normalize_pmcid(pmcid: Optional[str]) -> Optional[str]:
    """PMC ids as "PMC1234567": OpenAlex and Semantic Scholar give the bare
    number, PubMed and Europe PMC the prefixed form."""
    if not pmcid:
        return None
    s = str(pmcid).strip().rsplit("/", 1)[-1].upper()
    if s.startswith("PMC"):
        s = s[3:]
    return f"PMC{s}" if s else None


# ---------- record insertion ----------

def insert_source_hit(
    conn: sqlite3.Connection,
    *,
    source: str,
    source_id: str,
    query_id: Optional[str],
    raw: dict,
    title: str,
    abstract: Optional[str],
    authors: list[dict],
    year: Optional[int],
    doi: Optional[str] = None,
    pmid: Optional[str] = None,
    pmcid: Optional[str] = None,
    openalex_id: Optional[str] = None,
    venue: Optional[str] = None,
    document_type: Optional[str] = None,
    language: Optional[str] = None,
    keywords: Optional[list[str]] = None,
    url: Optional[str] = None,
    from_seed: bool = False,
    tldr: Optional[str] = None,
    snowball_rank: Optional[int] = None,
    native_relevance_score: Optional[float] = None,
) -> int:
    """Insert a hit. Either creates a new record or attaches to an existing one
    via DOI/PMID/OpenAlex match. Fuzzy title dedup happens later in 03_dedup.py.
    Returns the record_id."""
    doi = normalize_doi(doi)
    pmcid = normalize_pmcid(pmcid)
    title_norm = normalize_title(title)
    first_author = (authors[0].get("family") if authors else None) or None

    # Try ID-based match first
    existing = None
    for col, val in [("doi", doi), ("pmid", pmid),
                     ("pmcid", pmcid), ("openalex_id", openalex_id)]:
        if val:
            row = conn.execute(
                f"SELECT id, canonical_id FROM records WHERE {col} = ?", (val,)
            ).fetchone()
            if row:
                existing = row
                break

    if existing:
        record_id = existing["id"]
        # Backfill any missing IDs we now know
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
            "ELSE snowball_rank END, "
            # A record hit by multiple sources keeps the strongest evidence
            # of relevance seen so far (native scores aren't cross-source
            # comparable, but "higher within the same source" is meaningful).
            "native_relevance_score = CASE "
            "WHEN native_relevance_score IS NULL THEN ? "
            "WHEN ? IS NULL THEN native_relevance_score "
            "WHEN ? > native_relevance_score THEN ? "
            "ELSE native_relevance_score END "
            "WHERE id = ?",
            (
                doi, pmid, pmcid, openalex_id, abstract, tldr,
                1 if from_seed else 0,
                snowball_rank, snowball_rank, snowball_rank, snowball_rank,
                native_relevance_score, native_relevance_score,
                native_relevance_score, native_relevance_score,
                record_id,
            )
        )
    else:
        # New canonical record. Insert-then-update pattern: insert with a
        # placeholder canonical_id (will be replaced), use lastrowid for the
        # final canonical_id, update. This avoids the race where two writers
        # both read MAX(id) before either inserts.
        #
        # canonical_id is rec_{lastrowid:06d} — guaranteed unique because
        # lastrowid is the SQLite-assigned row id, which is monotonic and
        # unique even under concurrent inserts.
        #
        # We use a temporary placeholder canonical_id of f"_pending_{rand()}"
        # to satisfy the NOT NULL + UNIQUE constraints during insert.
        # If two concurrent inserts somehow collide on canonical_id (extremely
        # rare — would require lastrowid recycling), retry once with a
        # deterministic offset.
        import random as _rand
        for attempt in range(3):
            placeholder = f"_pending_{_rand.randint(0, 1 << 60):x}"
            try:
                cur = conn.execute(
                    "INSERT INTO records "
                    "(canonical_id, doi, pmid, pmcid, openalex_id, title, title_norm, "
                    "abstract, authors_json, first_author, year, venue, document_type, "
                    "language, keywords_json, url, from_seed, tldr, snowball_rank, "
                    "native_relevance_score) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (placeholder, doi, pmid, pmcid, openalex_id, title, title_norm,
                     abstract, json.dumps(authors), first_author, year, venue,
                     document_type, language,
                     json.dumps(keywords) if keywords else None, url,
                     1 if from_seed else 0, tldr, snowball_rank,
                     native_relevance_score)
                )
                record_id = cur.lastrowid
                canonical_id = f"rec_{record_id:06d}"
                conn.execute(
                    "UPDATE records SET canonical_id = ? WHERE id = ?",
                    (canonical_id, record_id)
                )
                break
            except sqlite3.IntegrityError as e:
                # If the placeholder collided (cosmically unlikely) or the
                # final canonical_id collided (would happen if a v0.4-era
                # rec_NNN already exists at this row id position), retry
                # with a different placeholder.
                if attempt == 2:
                    raise sqlite3.IntegrityError(
                        f"canonical_id collision after 3 attempts: {e}"
                    )
                continue

    # Insert the source hit (idempotent on UNIQUE (source, source_id))
    conn.execute(
        "INSERT OR IGNORE INTO source_hits "
        "(record_id, source, source_id, query_id, raw_json) "
        "VALUES (?,?,?,?,?)",
        (record_id, source, source_id, query_id, json.dumps(raw))
    )
    return record_id


def log_event(conn: sqlite3.Connection, stage: str, level: str,
              message: str, payload: Optional[dict] = None) -> None:
    conn.execute(
        "INSERT INTO events (stage, level, message, payload_json) VALUES (?,?,?,?)",
        (stage, level, message, json.dumps(payload) if payload else None)
    )


def record_query(conn: sqlite3.Connection, query_id: str, source: str,
                 query_string: str, filters: Optional[dict],
                 result_count: int, notes: str = "") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO queries "
        "(query_id, source, query_string, filters_json, result_count, notes) "
        "VALUES (?,?,?,?,?,?)",
        (query_id, source, query_string,
         json.dumps(filters) if filters else None, result_count, notes)
    )


# ---------- local relevance scoring ----------

def backfill_local_relevance_scores(conn: sqlite3.Connection, paths: "ProjectPaths") -> dict:
    """Score every record missing `relevance_score` via local sentence
    embeddings vs. the project's seed papers. See slr_engine/relevance.py.

    Returns a status dict: {"scored": int, "skipped_reason": str|None}.
    Never raises -- scoring is a best-effort triage aid, not a hard
    dependency of the pipeline (missing sentence-transformers or missing
    seeds both degrade to a no-op, not a failure).
    """
    from . import relevance as rel

    if not rel.available():
        return {"scored": 0, "skipped_reason": "sentence-transformers not installed"}

    reference_texts = rel.build_reference_texts(paths)
    if not reference_texts:
        return {"scored": 0, "skipped_reason": "no seed text available yet"}

    rows = conn.execute(
        "SELECT id, title, abstract FROM records WHERE relevance_score IS NULL"
    ).fetchall()
    if not rows:
        return {"scored": 0, "skipped_reason": None}

    scorer = rel.LocalRelevanceScorer(reference_texts)
    batch = [(r["title"] or "", r["abstract"]) for r in rows]
    scores = scorer.score_batch(batch)

    conn.executemany(
        "UPDATE records SET relevance_score = ? WHERE id = ?",
        [(score, row["id"]) for row, score in zip(rows, scores)],
    )
    return {"scored": len(rows), "skipped_reason": None}
