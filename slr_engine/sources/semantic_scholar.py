"""Semantic Scholar Academic Graph adapter."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator, Optional

from . import NormalizedRecord, SourceAdapter
from ..env import load_dotenv


load_dotenv()


def _friendly_error(e: Exception, api_key_set: bool) -> str:
    """Semantic Scholar's unauthenticated tier shares a GLOBAL pool (5000
    req/5min across every unauthenticated caller, not just this project),
    and /paper/search is capped at 1 req/sec even with a key. A 429 here
    often means the shared pool was already exhausted by other traffic --
    exponential backoff helps but can't guarantee success without a private
    quota. Point at the actual fix rather than just reporting the failure.
    """
    msg = f"{type(e).__name__}: {e}"
    if isinstance(e, urllib.error.HTTPError) and e.code == 429 and not api_key_set:
        msg += (
            " -- unauthenticated Semantic Scholar requests share a global "
            "rate pool; a free S2_API_KEY (request at "
            "https://www.semanticscholar.org/product/api#api-key-form) "
            "gives a private, more reliable quota instead of competing for "
            "the shared one. Set S2_API_KEY in .env."
        )
    return msg


FIELDS = ",".join([
    "paperId", "title", "abstract", "year", "authors", "venue",
    "externalIds", "openAccessPdf", "tldr", "citationCount",
    "referenceCount", "fieldsOfStudy", "publicationTypes", "publicationDate",
])


class SemanticScholarAdapter(SourceAdapter):
    name = "semantic_scholar"
    base = "https://api.semanticscholar.org/graph/v1/paper"

    def __init__(self, contact_email: Optional[str] = None,
                 user_agent: str = "slr-engine/1.0 (research; OA only)"):
        super().__init__(contact_email=contact_email, user_agent=user_agent)
        self.api_key = os.environ.get("S2_API_KEY")

    def _headers(self) -> dict:
        headers = {"User-Agent": self.user_agent}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _fetch_json(self, url: str, max_retries: int = 5) -> dict:
        """GET url as JSON, retrying rate limits and transient failures.

        S2's unauthenticated tier rate-limits aggressively; a single 429 used
        to abort the whole search, so this allows one more retry than the
        other adapters (2s, 4s, 8s, 16s, 32s).
        """
        req = urllib.request.Request(url, headers=self._headers())
        return json.loads(self._fetch(req, max_retries=max_retries))

    def search(
        self,
        query: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        languages: Optional[list[str]] = None,
        max_records: int = 1000,
    ) -> Iterator[NormalizedRecord]:
        year_filter = None
        if date_from or date_to:
            start = (date_from or "").split("-", 1)[0] or "*"
            end = (date_to or "").split("-", 1)[0] or "*"
            year_filter = f"{start}-{end}"

        fetched = 0
        offset = 0
        while fetched < max_records:
            limit = min(100, max_records - fetched)
            params = {
                "query": query,
                "limit": str(limit),
                "offset": str(offset),
                "fields": FIELDS,
            }
            if year_filter:
                params["year"] = year_filter
            url = f"{self.base}/search?{urllib.parse.urlencode(params)}"
            try:
                data = self._fetch_json(url)
            except Exception as e:
                self._record_error(
                    f"semantic_scholar request failed: "
                    f"{_friendly_error(e, bool(self.api_key))} (url={url[:200]})"
                )
                return

            rows = data.get("data") or []
            if not rows:
                return
            for paper in rows:
                rec = self._to_record(paper)
                if rec.title:
                    yield rec
                    fetched += 1
                    if fetched >= max_records:
                        return
            offset += len(rows)
            self._polite_sleep(0.2 if self.api_key else 1.0)

    def get_paper_edges(self, paper_id: str, direction: str, limit: int = 1000) -> list[dict]:
        """Return S2 citation/reference edge metadata for ranking snowball hits."""
        if direction not in {"citations", "references"}:
            raise ValueError("direction must be citations or references")
        fields = ",".join([
            "contexts", "intents", "isInfluential",
            "citingPaper.paperId", "citingPaper.externalIds",
            "citedPaper.paperId", "citedPaper.externalIds",
        ])
        out: list[dict] = []
        offset = 0
        while len(out) < limit:
            n = min(1000, limit - len(out))
            params = {"fields": fields, "offset": str(offset), "limit": str(n)}
            url = f"{self.base}/{paper_id}/{direction}?{urllib.parse.urlencode(params)}"
            try:
                data = self._fetch_json(url)
            except Exception as e:
                self._record_error(
                    f"semantic_scholar {direction} failed: "
                    f"{_friendly_error(e, bool(self.api_key))}"
                )
                return out
            rows = data.get("data") or []
            if not rows:
                return out
            out.extend(rows)
            if not data.get("next"):
                return out
            offset = int(data["next"])
            self._polite_sleep(0.2 if self.api_key else 1.0)
        return out

    def lookup_by_doi(self, doi: str) -> Optional[dict]:
        url = f"{self.base}/DOI:{urllib.parse.quote(doi)}?fields=paperId,externalIds"
        try:
            return self._fetch_json(url)
        except Exception as e:
            self._record_error(
                f"semantic_scholar DOI lookup failed: {_friendly_error(e, bool(self.api_key))}"
            )
            return None

    def _to_record(self, p: dict) -> NormalizedRecord:
        external = p.get("externalIds") or {}
        authors = []
        for a in p.get("authors") or []:
            name = a.get("name") or ""
            parts = name.rsplit(" ", 1)
            authors.append({
                "family": parts[-1] if parts else "",
                "given": parts[0] if len(parts) == 2 else "",
            })
        tldr_obj = p.get("tldr") or {}
        pdf = p.get("openAccessPdf") or {}
        return NormalizedRecord(
            source="semantic_scholar",
            source_id=p.get("paperId") or external.get("DOI") or p.get("title", "")[:80],
            title=p.get("title") or "",
            abstract=p.get("abstract"),
            authors=authors,
            year=p.get("year"),
            doi=external.get("DOI"),
            pmid=external.get("PubMed"),
            pmcid=external.get("PubMedCentral"),
            venue=p.get("venue"),
            document_type=(p.get("publicationTypes") or [None])[0],
            keywords=p.get("fieldsOfStudy"),
            url=(pdf.get("url") if isinstance(pdf, dict) else None),
            tldr=tldr_obj.get("text") if isinstance(tldr_obj, dict) else None,
            raw=p,
        )
