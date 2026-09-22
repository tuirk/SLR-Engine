"""Query validator.

Catches structurally broken queries before they hit the network. Two-tier:

  - Errors block: clear malformed queries that won't return useful results.
    Example: a flat keyword list with no Boolean operators on OpenAlex —
    that gets interpreted as implicit AND across every term and returns 0.

  - Warnings prompt: queries that are syntactically valid but suspicious.
    Example: a concept group with only one synonym (likely under-specified).
    The agent surfaces the warning to the user; user explicit okay required.

The validator runs from `02_search_open.py` before any HTTP request.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Union


# A query that doesn't contain any of these is suspect for OpenAlex
_BOOL_OPS_RE = re.compile(r"\b(?:AND|OR|NOT)\b")


@dataclass
class ValidationResult:
    """Outcome of validating one source's query."""
    source: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)

    def render(self) -> str:
        lines = [f"[{self.source}]"]
        for e in self.errors:
            lines.append(f"  ERROR: {e}")
        for w in self.warnings:
            lines.append(f"  WARN:  {w}")
        if not self.errors and not self.warnings:
            lines.append("  ok")
        return "\n".join(lines)


def _count_meaningful_tokens(s: str) -> int:
    """Count tokens that aren't operators, punctuation, or single chars."""
    if not s:
        return 0
    # Strip quoted phrases first; treat each as one token
    quoted = re.findall(r'"([^"]*)"', s)
    count = sum(1 for q in quoted if q.strip() and q.strip().upper() not in ("AND", "OR", "NOT"))
    stripped = re.sub(r'"[^"]*"', " ", s)
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]+", stripped)
    count += sum(1 for t in tokens if t.upper() not in ("AND", "OR", "NOT") and len(t) > 2)
    return count


def _balance_parens(s: str) -> bool:
    depth = 0
    for c in s:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def validate_openalex(query: str) -> ValidationResult:
    """OpenAlex query rules.

    OpenAlex's `search=` parameter does Boolean-aware tokenization. Without
    explicit operators, it interprets a multi-token query as relevance-ranked
    matching where ALL tokens contribute to the score — but an implicit AND
    across every literal token will collapse to 0 hits on long flat lists,
    which is the most common v0.4 failure mode.
    """
    r = ValidationResult(source="openalex")
    if not query or not query.strip():
        r.errors.append("query is empty")
        return r

    if not _balance_parens(query):
        r.errors.append("unbalanced parentheses")

    has_ops = bool(_BOOL_OPS_RE.search(query))
    n_tokens = _count_meaningful_tokens(query)

    if not has_ops and n_tokens > 5:
        r.errors.append(
            f"flat keyword list of {n_tokens} terms with no Boolean operators "
            "(OR/AND). OpenAlex will interpret this as implicit AND across all "
            "terms and likely return 0 hits. Use explicit OR within concept "
            "groups and AND between groups."
        )

    if not has_ops and 2 <= n_tokens <= 5:
        r.warnings.append(
            f"{n_tokens} terms with no Boolean operators. May be interpreted "
            "more loosely than intended. Consider explicit OR/AND structure."
        )

    # Concept-group sanity: count the AND-separated groups
    if "AND" in query.upper():
        # Split on AND at top level (not inside parens)
        groups = _split_at_and(query)
        if groups:
            for i, g in enumerate(groups, 1):
                token_count = _count_meaningful_tokens(g)
                if token_count == 0:
                    r.errors.append(
                        f"AND-group #{i} is empty: {g[:80]!r}"
                    )
                elif token_count == 1 and "OR" not in g.upper():
                    r.warnings.append(
                        f"AND-group #{i} has only one term, no synonyms. "
                        f"Consider widening: {g[:80]!r}"
                    )

    return r


def validate_crossref(query: Union[str, dict]) -> ValidationResult:
    """Crossref query rules.

    Crossref does relevance ranking, not strict Boolean. The structured
    params shape is strongly preferred over flat strings:
      - filter: dict of strict filters (date, type, container, etc.)
      - query.bibliographic: list of narrow concept groups (relevance-ranked)
      - query.title: even narrower title-only signal
    """
    r = ValidationResult(source="crossref")

    if isinstance(query, str):
        if not query.strip():
            r.errors.append("query is empty")
            return r
        n_tokens = _count_meaningful_tokens(query)
        has_ops = bool(_BOOL_OPS_RE.search(query))
        if n_tokens > 5 and not has_ops:
            r.warnings.append(
                f"flat string query with {n_tokens} terms and no Boolean "
                "operators. Crossref relevance-ranks against this — likely "
                "to pull in many off-topic papers on common-keyword topics. "
                "Consider switching to structured params with `filter` for "
                "strict filtering and `query.bibliographic` for narrow "
                "concept groups."
            )
        return r

    if not isinstance(query, dict):
        r.errors.append(
            f"query must be string or dict, got {type(query).__name__}"
        )
        return r

    has_query_param = any(
        k in query for k in (
            "query.bibliographic", "query.title", "query",
            "query.author", "query.container-title", "query.affiliation",
        )
    )
    has_filter = bool(query.get("filter"))

    if not has_query_param and not has_filter:
        r.errors.append(
            "no query.* params and no filter — query is empty"
        )

    qb = query.get("query.bibliographic")
    if qb is not None:
        if isinstance(qb, str):
            if not qb.strip():
                r.errors.append("query.bibliographic is empty string")
        elif isinstance(qb, list):
            if not qb:
                r.errors.append("query.bibliographic is empty list")
            for i, g in enumerate(qb, 1):
                if not isinstance(g, str) or not g.strip():
                    r.errors.append(
                        f"query.bibliographic[{i}] is not a non-empty string"
                    )
        else:
            r.errors.append(
                f"query.bibliographic must be string or list, got "
                f"{type(qb).__name__}"
            )

    flt = query.get("filter")
    if flt is not None and not isinstance(flt, dict):
        r.errors.append(
            f"`filter` must be a dict, got {type(flt).__name__}"
        )

    # Soft suggestions about good practice
    if isinstance(qb, str) and _count_meaningful_tokens(qb) > 8:
        r.warnings.append(
            "query.bibliographic is a long flat string. Crossref's relevance "
            "ranking on long strings can pull in tangentially-related "
            "literatures. Consider splitting into multiple narrower groups "
            "(list of strings) — Crossref AND's repeated query.bibliographic "
            "params for ranking."
        )
    if not has_filter:
        r.warnings.append(
            "no `filter` block. Filters (type, date range, has-abstract, "
            "container-title) are STRICT in Crossref — they actually exclude "
            "non-matching records. Consider at minimum a `filter.type` and "
            "the engine's date filters."
        )

    return r


def validate_pubmed(query: str) -> ValidationResult:
    """PubMed query rules.

    PubMed uses Entrez query syntax with field tags like [tiab] (title/abstract).
    The most common failure modes are unbalanced parens and empty groups.
    """
    r = ValidationResult(source="pubmed")
    if not query or not query.strip():
        r.errors.append("query is empty")
        return r

    if not _balance_parens(query):
        r.errors.append("unbalanced parentheses")

    # Balanced quotes
    if query.count('"') % 2 != 0:
        r.errors.append("unbalanced double quotes")

    if "AND" in query.upper():
        groups = _split_at_and(query)
        for i, g in enumerate(groups, 1):
            if _count_meaningful_tokens(g) == 0:
                r.errors.append(
                    f"AND-group #{i} is empty: {g[:80]!r}"
                )

    return r


def validate_europe_pmc(query: str) -> ValidationResult:
    """Europe PMC query rules.

    Europe PMC uses field-prefixed queries like TITLE_ABS:"foo".
    Same parenthesis/quote balance rules as PubMed.
    """
    r = ValidationResult(source="europe_pmc")
    if not query or not query.strip():
        r.errors.append("query is empty")
        return r

    if not _balance_parens(query):
        r.errors.append("unbalanced parentheses")

    if query.count('"') % 2 != 0:
        r.errors.append("unbalanced double quotes")

    if "AND" in query.upper():
        groups = _split_at_and(query)
        for i, g in enumerate(groups, 1):
            if _count_meaningful_tokens(g) == 0:
                r.errors.append(
                    f"AND-group #{i} is empty: {g[:80]!r}"
                )
    return r


def validate_arxiv(query: str) -> ValidationResult:
    """arXiv query rules.

    arXiv uses prefix syntax (ti:, abs:, all:, cat:). Balance and
    non-empty checks.
    """
    r = ValidationResult(source="arxiv")
    if not query or not query.strip():
        r.errors.append("query is empty")
        return r
    if not _balance_parens(query):
        r.errors.append("unbalanced parentheses")
    return r


def validate_semantic_scholar(query: str) -> ValidationResult:
    """Semantic Scholar /paper/search rules.

    The endpoint is plain-text relevance search with no query syntax:
    AND/OR/NOT are matched as ordinary words, quotes and parentheses are
    ignored, and hyphenated terms match nothing (the API docs say to write
    them with spaces). OpenAlex-style Boolean groups therefore return noise
    or zero results.
    """
    r = ValidationResult(source="semantic_scholar")
    if not query or not query.strip():
        r.errors.append("query is empty")
        return r

    if _BOOL_OPS_RE.search(query):
        r.errors.append(
            "Boolean operators (AND/OR/NOT) are not supported: Semantic "
            "Scholar's relevance search matches them as ordinary words. Use "
            "a short plain-text phrase such as the review's core term."
        )
    hyphenated = sorted(set(re.findall(r"\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b", query)))
    if hyphenated:
        r.errors.append(
            "hyphenated terms match nothing on Semantic Scholar; write them "
            f"with spaces: {', '.join(hyphenated[:5])}"
        )
    if re.search(r'["()]', query):
        r.warnings.append(
            "quotes and parentheses are ignored by Semantic Scholar's "
            "relevance search"
        )
    n_tokens = _count_meaningful_tokens(query)
    if n_tokens > 8:
        r.warnings.append(
            f"{n_tokens} terms: long plain-text queries drift in relevance "
            "ranking. Keep to a few distinctive words."
        )
    return r


def _validate_simple_ranked(source: str, query: str) -> ValidationResult:
    r = ValidationResult(source=source)
    if not query or not query.strip():
        r.errors.append("query is empty")
        return r
    if not _balance_parens(query):
        r.warnings.append("unbalanced parentheses; this source treats them as literal/noisy text")
    n_tokens = _count_meaningful_tokens(query)
    if n_tokens > 12:
        r.warnings.append(
            f"{n_tokens} meaningful terms in a simple ranked-search source. "
            "Use a shorter distinctive keyword string."
        )
    return r


def validate_dblp(query: str) -> ValidationResult:
    """DBLP simple ranked search."""
    return _validate_simple_ranked("dblp", query)


def validate_ia_scholar(query: str) -> ValidationResult:
    """Internet Archive Scholar simple ranked search."""
    return _validate_simple_ranked("ia_scholar", query)


def _split_at_and(s: str) -> list[str]:
    """Split a query string at top-level AND (not inside parens)."""
    parts: list[str] = []
    depth = 0
    last = 0
    s_upper = s.upper()
    i = 0
    while i < len(s):
        c = s[i]
        if c == "(":
            depth += 1
            i += 1
            continue
        if c == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0 and s_upper[i:i + 5] == " AND ":
            parts.append(s[last:i].strip())
            last = i + 5
            i += 5
            continue
        i += 1
    parts.append(s[last:].strip())
    return [p for p in parts if p]


# ---------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------

VALIDATORS = {
    "openalex": validate_openalex,
    "crossref": validate_crossref,
    "pubmed": validate_pubmed,
    "europe_pmc": validate_europe_pmc,
    "arxiv": validate_arxiv,
    "semantic_scholar": validate_semantic_scholar,
    "dblp": validate_dblp,
    "ia_scholar": validate_ia_scholar,
}


def validate(source: str, query: Union[str, dict]) -> ValidationResult:
    """Validate a query for the given source. Falls back to a no-op
    result for unknown sources."""
    fn = VALIDATORS.get(source)
    if fn is None:
        return ValidationResult(source=source)
    return fn(query)


def validate_all(queries: dict) -> list[ValidationResult]:
    """Validate a dict of {source: query}. Returns one result per source."""
    return [validate(source, q) for source, q in queries.items()]


def render_summary(results: list[ValidationResult]) -> str:
    """Pretty-print all results."""
    lines = []
    for r in results:
        lines.append(r.render())
        lines.append("")
    return "\n".join(lines).strip()


def has_blocking_errors(results: list[ValidationResult]) -> bool:
    return any(r.has_errors for r in results)


def has_warnings(results: list[ValidationResult]) -> bool:
    return any(r.has_warnings for r in results)
