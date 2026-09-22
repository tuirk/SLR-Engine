"""SQLite schema for an SLR project."""

SCHEMA_SQL = """
-- Canonical record table. One row per unique work after dedup.
CREATE TABLE IF NOT EXISTS records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id    TEXT UNIQUE NOT NULL,   -- our internal id, e.g. "rec_000123"
    doi             TEXT,
    pmid            TEXT,
    pmcid           TEXT,
    openalex_id     TEXT,
    title           TEXT NOT NULL,
    title_norm      TEXT NOT NULL,          -- normalized for fuzzy match
    abstract        TEXT,
    authors_json    TEXT,                   -- JSON list of {family, given}
    first_author    TEXT,
    year            INTEGER,
    venue           TEXT,
    document_type   TEXT,
    language        TEXT,
    keywords_json   TEXT,
    url             TEXT,
    from_seed       INTEGER NOT NULL DEFAULT 0,
    tldr            TEXT,
    snowball_rank   INTEGER,
    oa_status       TEXT,                   -- gold, diamond, hybrid, green, bronze, closed, unknown
    oa_url          TEXT,
    license         TEXT,
    native_relevance_score REAL,            -- source's own ranking score (raw scale, NOT comparable across sources); used only for early-stop pagination
    relevance_score REAL,                   -- local sentence-embedding cosine similarity vs. seed reference (0..1, comparable across all sources)
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_records_doi ON records(doi);
CREATE INDEX IF NOT EXISTS idx_records_pmid ON records(pmid);
CREATE INDEX IF NOT EXISTS idx_records_openalex ON records(openalex_id);
CREATE INDEX IF NOT EXISTS idx_records_title_norm ON records(title_norm);

-- Each source hit. Many-to-one with records.
-- A single record can be returned by multiple databases; we keep all hits
-- so we can audit which databases yielded which papers.
CREATE TABLE IF NOT EXISTS source_hits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id       INTEGER NOT NULL,
    source          TEXT NOT NULL,          -- openalex, crossref, pubmed, europepmc, core, scopus, wos
    source_id       TEXT,                   -- the source's native id
    query_id        TEXT,                   -- references queries(query_id)
    raw_json        TEXT,                   -- raw record from source for audit
    fetched_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (record_id) REFERENCES records(id) ON DELETE CASCADE,
    UNIQUE (source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_source_hits_record ON source_hits(record_id);
CREATE INDEX IF NOT EXISTS idx_source_hits_source ON source_hits(source);

-- Queries we ran. Frozen for audit.
CREATE TABLE IF NOT EXISTS queries (
    query_id        TEXT PRIMARY KEY,       -- e.g. "openalex_2026_05_06_001"
    source          TEXT NOT NULL,
    query_string    TEXT NOT NULL,
    filters_json    TEXT,
    executed_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    result_count    INTEGER,
    notes           TEXT
);

-- Screening decisions. One row per (record, screening_pass).
CREATE TABLE IF NOT EXISTS screening (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id       INTEGER NOT NULL,
    pass            TEXT NOT NULL,          -- "title_abstract", "full_text"
    decision        TEXT NOT NULL,          -- "include", "exclude", "unsure"
    reason          TEXT,
    criteria_hit    TEXT,                   -- JSON list, e.g. ["E1", "I2"]
    decided_by      TEXT NOT NULL,          -- "agent" or "human"
    decided_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    batch_id        TEXT,
    FOREIGN KEY (record_id) REFERENCES records(id) ON DELETE CASCADE,
    UNIQUE (record_id, pass, decided_by)
);

CREATE INDEX IF NOT EXISTS idx_screening_record ON screening(record_id);
CREATE INDEX IF NOT EXISTS idx_screening_decision ON screening(decision);

-- Dedup audit. Records why two source_hits collapsed into one record.
CREATE TABLE IF NOT EXISTS dedup_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id    TEXT NOT NULL,
    merged_source   TEXT NOT NULL,
    merged_source_id TEXT,
    match_method    TEXT NOT NULL,          -- "doi", "pmid", "openalex_id", "fuzzy_title"
    match_confidence REAL,
    decided_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Full-text download attempts.
CREATE TABLE IF NOT EXISTS downloads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id       INTEGER NOT NULL,
    resolver_source TEXT NOT NULL,          -- pmc, europepmc, openalex, unpaywall, arxiv, core, crossref
    url             TEXT NOT NULL,
    license         TEXT,
    file_path       TEXT,                   -- relative path under data/fulltext/
    file_format     TEXT,                   -- pdf, xml, html, text (as detected once downloaded)
    status          TEXT NOT NULL,          -- resolved, queued, success, failed, skipped_closed, skipped_superseded
    error           TEXT,
    fetched_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (record_id) REFERENCES records(id) ON DELETE CASCADE
);

-- Snowball provenance. When a record was discovered via snowballing,
-- this table records which seed paper led to it and the direction
-- (backward = via references; forward = via citations).
CREATE TABLE IF NOT EXISTS snowball_links (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    seed_record_id  INTEGER NOT NULL,           -- the included paper we walked from
    found_record_id INTEGER NOT NULL,           -- the paper we discovered
    direction       TEXT NOT NULL,              -- "backward" or "forward"
    iteration       INTEGER NOT NULL DEFAULT 1, -- 1, 2 if doing 2nd-degree
    discovered_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (seed_record_id) REFERENCES records(id) ON DELETE CASCADE,
    FOREIGN KEY (found_record_id) REFERENCES records(id) ON DELETE CASCADE,
    UNIQUE (seed_record_id, found_record_id, direction)
);

CREATE INDEX IF NOT EXISTS idx_snowball_seed ON snowball_links(seed_record_id);
CREATE INDEX IF NOT EXISTS idx_snowball_found ON snowball_links(found_record_id);

-- Free-form event log. Anything worth auditing later.
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stage           TEXT NOT NULL,          -- search, dedup, screen, resolve, download, export, snowball, extract
    level           TEXT NOT NULL,          -- info, warn, error
    message         TEXT NOT NULL,
    payload_json    TEXT,
    occurred_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Structured extraction from a paper. Produced during full-text screening
-- (free, since the LLM is already reading the paper anyway) or as a
-- post-hoc pass via 08_extract.py / 08b_quality_pass.py.
--
-- Fields are stored as JSON because the schema is configurable per project
-- (project.yaml's `extraction.custom_fields`). Quality fields have their
-- own dedicated columns because they're standardized across projects.
CREATE TABLE IF NOT EXISTS extractions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id         INTEGER NOT NULL,
    fields_json       TEXT,                  -- JSON object: {field_name: value}
                                              -- See slr_engine/llm.py GENERIC_EXTRACTION_FIELDS
                                              -- for the default schema.
    -- Quality assessment fields (populated only if --with-quality was set).
    -- Domain-neutral, designed for AI/ML/tech/finance/maths.
    methodological_rigor    TEXT,            -- 'high' | 'medium' | 'low' | 'unclear' | NULL
    evidence_strength       TEXT,            -- 'strong' | 'moderate' | 'weak' | 'unclear' | NULL
    limitations_acknowledged TEXT,           -- 'yes' | 'partial' | 'no' | NULL
    quality_notes           TEXT,            -- 1-2 sentences flagging concerns or strengths
    -- PRISMA 2020 uses "risk of bias", not generic "quality assessment".
    -- These columns store a domain-based RoB appraisal for each included study.
    risk_of_bias_tool       TEXT,            -- rubric/tool used, e.g. RoB 2, ROBINS-I, or project rubric
    risk_of_bias_overall    TEXT,            -- 'low' | 'some_concerns' | 'high' | 'unclear' | NULL
    risk_of_bias_domains_json TEXT,          -- JSON list of domain-level judgements + rationales
    risk_of_bias_notes      TEXT,            -- concise justification for overall judgement
    extracted_by      TEXT NOT NULL,         -- 'agent' | 'human' | 'llm:<provider>'
    extracted_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_text_chars INTEGER,               -- how much of the paper the extractor saw
    FOREIGN KEY (record_id) REFERENCES records(id) ON DELETE CASCADE,
    UNIQUE (record_id, extracted_by)
);

CREATE INDEX IF NOT EXISTS idx_extractions_record ON extractions(record_id);
"""
