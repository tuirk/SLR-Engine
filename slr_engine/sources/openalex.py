"""OpenAlex adapter.

Docs:
- https://docs.openalex.org/api-entities/works/search-works
- https://docs.openalex.org/api-entities/works/filter-works
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Iterator, Optional

from . import SourceAdapter, NormalizedRecord

# OpenAlex rejects URLs whose query string exceeds ~4096 chars. Long Boolean
# queries must be split into chunks and merged client-side.
_MAX_QUERY_CHARS = 3800

# OpenAlex's `search=` has no minimum-score floor: it returns every work with
# ANY term overlap, ranked by `relevance_score`, all the way down to noise.
# Empirically (vibe-coding-research-2026, 2026-08-06): top score ~211, and
# results stayed genuinely on-topic down to ratio ~0.05-0.07 (rank ~100-110);
# below that the tail drifted into unrelated fields fast. Default cutoff is
# deliberately a bit below that observed break so it doesn't clip borderline
# hits, while still stopping well before the score decays into irrelevance.
_DEFAULT_MIN_RELEVANCE_RATIO = 0.03


def _reconstruct_abstract(inverted: Optional[dict]) -> Optional[str]:
    """OpenAlex returns abstracts as inverted indices for licensing reasons."""
    if not inverted:
        return None
    positions = []
    for word, idxs in inverted.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions) or None


def _split_top_level(s: str, op: str) -> list[str]:
    """Split ``s`` at `` op `` occurrences outside parentheses and quotes."""
    parts: list[str] = []
    depth = 0
    in_quote = False
    last = 0
    token = f" {op} "
    i = 0
    while i < len(s):
        c = s[i]
        if c == '"':
            in_quote = not in_quote
        elif not in_quote and c == "(":
            depth += 1
        elif not in_quote and c == ")":
            depth -= 1
        elif not in_quote and depth == 0 and s.startswith(token, i):
            parts.append(s[last:i].strip())
            i += len(token)
            last = i
            continue
        i += 1
    parts.append(s[last:].strip())
    return [p for p in parts if p]


def _unwrap(group: str) -> str:
    """Drop one pair of parentheses enclosing the whole group, if present."""
    if not (group.startswith("(") and group.endswith(")")):
        return group
    depth = 0
    for i, c in enumerate(group):
        depth += c == "("
        depth -= c == ")"
        if depth == 0 and i < len(group) - 1:
            return group          # "(a) OR (b)": the first paren closes early
    return group[1:-1].strip()


def _split_query(query: str, max_chars: int = _MAX_QUERY_CHARS) -> list[str]:
    """Split a Boolean query too long for one request into queries whose
    results, merged, match the original.

    For (A1 OR A2 ...) AND (B1 OR ...) AND ..., the terms of the largest
    OR-group are spread across copies of the query that keep every other
    group intact: (A1 OR A2) AND B, (A3 OR A4) AND B, ... Splitting at
    arbitrary spaces instead would break the parentheses and drop concept
    groups from later chunks. A query without that shape is sent whole.
    """
    q = query.strip()
    if len(q) <= max_chars:
        return [q]
    q = " ".join(q.split())

    groups = _split_top_level(q, "AND")
    candidates = [
        i for i, g in enumerate(groups)
        if not g.upper().startswith("NOT ")
        and len(_split_top_level(_unwrap(g), "OR")) > 1
    ]
    if not candidates:
        return [q]
    idx = max(candidates, key=lambda i: len(groups[i]))
    terms = _split_top_level(_unwrap(groups[idx]), "OR")
    rest = len(q) - len(groups[idx])
    budget = max_chars - rest - 2          # the chunk's own parentheses

    chunks: list[list[str]] = [[]]
    for term in terms:
        if chunks[-1] and len(" OR ".join(chunks[-1] + [term])) > budget:
            chunks.append([])
        chunks[-1].append(term)
    if budget <= 0 or any(len(" OR ".join(c)) > budget for c in chunks):
        return [q]

    out = []
    for chunk in chunks:
        parts = list(groups)
        parts[idx] = "(" + " OR ".join(chunk) + ")"
        out.append(" AND ".join(parts))
    return out


class OpenAlexAdapter(SourceAdapter):
    name = "openalex"
    base = "https://api.openalex.org/works"

    def __init__(
        self,
        contact_email: Optional[str] = None,
        api_key: Optional[str] = None,
        require_abstract: bool = True,
        user_agent: str = "slr-engine/1.0 (research; OA only)",
        min_relevance_ratio: float = _DEFAULT_MIN_RELEVANCE_RATIO,
    ):
        super().__init__(contact_email=contact_email, user_agent=user_agent)
        self.api_key = api_key
        self.require_abstract = require_abstract
        self.min_relevance_ratio = min_relevance_ratio

    def search(
        self,
        query: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        languages: Optional[list[str]] = None,
        max_records: int = 1000,
    ) -> Iterator[NormalizedRecord]:
        seen: set[str] = set()
        fetched = 0
        for chunk in _split_query(query):
            for rec in self._search_chunk(
                chunk,
                date_from=date_from,
                date_to=date_to,
                languages=languages,
                max_records=max_records - fetched,
            ):
                if rec.source_id in seen:
                    continue
                seen.add(rec.source_id)
                yield rec
                fetched += 1
                if fetched >= max_records:
                    return

    def _search_chunk(
        self,
        query: str,
        *,
        date_from: Optional[str],
        date_to: Optional[str],
        languages: Optional[list[str]],
        max_records: int,
    ) -> Iterator[NormalizedRecord]:
        filters = []
        if date_from:
            filters.append(f"from_publication_date:{date_from}")
        if date_to:
            filters.append(f"to_publication_date:{date_to}")
        if languages:
            filters.append(f"language:{'|'.join(languages)}")
        if self.require_abstract:
            filters.append("has_abstract:true")

        params = {
            "search": query,
            "filter": ",".join(filters) if filters else None,
            "per-page": "200",
            "cursor": "*",
        }
        if self.contact_email:
            params["mailto"] = self.contact_email
        params = {k: v for k, v in params.items() if v is not None}

        fetched = 0
        top_score: Optional[float] = None
        while True:
            url = f"{self.base}?{urllib.parse.urlencode(params)}"
            headers = {"User-Agent": self.user_agent}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            req = urllib.request.Request(url, headers=headers)
            try:
                data = json.loads(self._fetch(req))
            except Exception as e:
                self._record_error(
                    f"openalex request failed: {type(e).__name__}: {e} "
                    f"(url={url[:200]})"
                )
                return

            for work in data.get("results", []):
                score = work.get("relevance_score")
                if score is not None:
                    if top_score is None:
                        top_score = score
                    elif top_score > 0 and score < top_score * self.min_relevance_ratio:
                        # Scores are sorted descending; once we're below the
                        # floor, every remaining result in this run is worse.
                        return
                yield self._to_record(work)
                fetched += 1
                if fetched >= max_records:
                    return

            next_cursor = data.get("meta", {}).get("next_cursor")
            if not next_cursor or not data.get("results"):
                return
            params["cursor"] = next_cursor
            self._polite_sleep(0.1)

    def _to_record(self, w: dict) -> NormalizedRecord:
        authors = []
        for a in w.get("authorships", []):
            au = a.get("author", {}) or {}
            name = au.get("display_name") or ""
            parts = name.rsplit(" ", 1)
            given = parts[0] if len(parts) == 2 else ""
            family = parts[-1]
            authors.append({"family": family, "given": given})

        ids = w.get("ids", {}) or {}
        pmid = None
        if ids.get("pmid"):
            pmid = ids["pmid"].rsplit("/", 1)[-1]
        pmcid = None
        if ids.get("pmcid"):
            pmcid = ids["pmcid"].rsplit("/", 1)[-1]

        venue = None
        loc = w.get("primary_location") or {}
        src = loc.get("source") or {}
        if src.get("display_name"):
            venue = src["display_name"]

        keywords = [
            k.get("display_name")
            for k in (w.get("keywords") or [])
            if k.get("display_name")
        ]

        return NormalizedRecord(
            source="openalex",
            source_id=w.get("id", "").rsplit("/", 1)[-1],
            title=w.get("title") or w.get("display_name") or "",
            abstract=_reconstruct_abstract(w.get("abstract_inverted_index")),
            authors=authors,
            year=w.get("publication_year"),
            doi=w.get("doi"),
            pmid=pmid,
            pmcid=pmcid,
            openalex_id=ids.get("openalex"),
            venue=venue,
            document_type=w.get("type"),
            language=w.get("language"),
            keywords=keywords or None,
            url=w.get("doi") or (loc.get("landing_page_url")),
            native_relevance_score=w.get("relevance_score"),
            raw=w,
        )
