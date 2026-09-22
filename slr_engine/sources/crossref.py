"""Crossref adapter.

Crossref's REST API does relevance ranking on free-text queries, not
strict Boolean filtering. To get useful results for systematic reviews:

  - Use `filter=` parameters for strictly-filterable conditions
    (date range, content type, journal/publisher, has-abstract, etc.).
    These are TRUE filters — they actually exclude non-matching records.

  - Use `query.bibliographic` ONLY for narrow concept groups with
    distinctive vocabulary (named entities, specific terms). Crossref
    relevance-ranks against `query.bibliographic`, so common keywords
    pull in unrelated literatures.

  - Optionally use `query.title` for an even tighter title-only signal.

The adapter accepts both old-style (a flat string `query`) and new-style
(a dict of structured params) input, for backward compatibility with
v0.4 query files. The new style is preferred.

Docs:
  - https://api.crossref.org/swagger-ui/index.html
  - https://www.crossref.org/documentation/retrieve-metadata/rest-api/
  - Filter list: https://api.crossref.org/swagger-ui/index.html#/Works/get_works
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Iterator, Optional, Union

from . import SourceAdapter, NormalizedRecord


_TAG_RE = re.compile(r"<[^>]+>")

# Crossref's query.bibliographic/query params do NOT support boolean
# operators or phrase quoting -- verified empirically (2026-08-06): quoted
# and unquoted versions of the same string returned identical total-results
# and identical top-5 items. It's a pure bag-of-words relevance search with
# no minimum-score floor, and it will happily paginate into results that
# share zero real topical overlap with the query (e.g. "coding" alone
# matching video-codec and medical-coding papers). The top of the ranking is
# reliable; the deep tail is not. Empirically the ranking held up well down
# to score ratio ~0.4-0.45 of the top result, then collapsed into noise by
# ratio ~0.42 and below.
_DEFAULT_MIN_RELEVANCE_RATIO = 0.4


def _strip_tags(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return _TAG_RE.sub("", s).strip() or None


class CrossrefAdapter(SourceAdapter):
    name = "crossref"
    base = "https://api.crossref.org/works"

    def __init__(
        self,
        contact_email: Optional[str] = None,
        user_agent: str = "slr-engine/1.0 (research; OA only)",
        min_relevance_ratio: float = _DEFAULT_MIN_RELEVANCE_RATIO,
    ):
        super().__init__(contact_email=contact_email, user_agent=user_agent)
        self.min_relevance_ratio = min_relevance_ratio

    def search(
        self,
        query: Union[str, dict],
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        languages: Optional[list[str]] = None,
        max_records: int = 1000,
    ) -> Iterator[NormalizedRecord]:
        """Search Crossref.

        `query` may be:
          - A string: legacy v0.4 behavior. Sent as a single `query=` param.
            Relevance-ranked, noisy on common-keyword topics. Discouraged.
          - A dict with structured Crossref params, e.g.:
              {
                "query.bibliographic": [
                    "<narrow concept group A>",
                    "<narrow concept group B>"
                ],
                "query.title": "<even tighter title-only filter>",
                "filter": {
                    "type": "journal-article",
                    "from-pub-date": "2020-01-01",
                    "has-abstract": "true"
                }
              }
            All keys are optional. `query.bibliographic` may be a string or
            a list (list -> repeated param, AND'd by Crossref for ranking).
            `filter` keys get merged with the engine's date filters.
        """
        params, _query_for_log = self._build_params(
            query, date_from=date_from, date_to=date_to,
            max_records=max_records,
        )

        headers = {"User-Agent": self.user_agent}
        if self.contact_email:
            headers["User-Agent"] += f" (mailto:{self.contact_email})"

        fetched = 0
        top_score: Optional[float] = None
        while True:
            url = f"{self.base}?{urllib.parse.urlencode(params, doseq=True)}"
            req = urllib.request.Request(url, headers=headers)
            try:
                data = json.loads(self._fetch(req))
            except Exception as e:
                self._record_error(
                    f"crossref request failed: {type(e).__name__}: {e} "
                    f"(url={url[:200]})"
                )
                return

            msg = data.get("message", {})
            items = msg.get("items", [])
            if not items:
                return

            for item in items:
                score = item.get("score")
                if score is not None:
                    if top_score is None:
                        top_score = score
                    elif top_score > 0 and score < top_score * self.min_relevance_ratio:
                        # Items come back sorted by score descending; once
                        # under the floor, the rest of this run only gets worse.
                        return
                rec = self._to_record(item)
                # Crossref's language metadata is unreliable; client-side filter
                if languages and rec.language and rec.language not in languages:
                    continue
                yield rec
                fetched += 1
                if fetched >= max_records:
                    return

            next_cursor = msg.get("next-cursor")
            if not next_cursor:
                return
            params["cursor"] = next_cursor
            self._polite_sleep(0.1)

    def _build_params(self,
                      query: Union[str, dict],
                      date_from: Optional[str],
                      date_to: Optional[str],
                      max_records: int) -> tuple[dict, str]:
        """Build the `urllib.urlencode` params dict from input.

        Returns (params_dict, human_readable_query_for_logging).
        """
        # Engine-level filters (date range) get merged with any user filters
        engine_filters: dict[str, str] = {}
        if date_from:
            engine_filters["from-pub-date"] = date_from
        if date_to:
            engine_filters["until-pub-date"] = date_to

        rows = "200"
        # With cursor paging Crossref does not sort by relevance unless told
        # to; without this the first page is an arbitrary slice of every
        # work sharing a word with the query, and the relevance floor in
        # search() is anchored on a random item's score.
        by_score = {"sort": "score", "order": "desc"}
        if isinstance(query, str):
            # Legacy: free-text query. Use as `query=`.
            params: dict = {
                "query": query,
                "rows": rows,
                "cursor": "*",
                **by_score,
            }
            if engine_filters:
                params["filter"] = ",".join(
                    f"{k}:{v}" for k, v in engine_filters.items()
                )
            if self.contact_email:
                params["mailto"] = self.contact_email
            return params, query

        if not isinstance(query, dict):
            raise ValueError(
                f"crossref query must be string or dict, got {type(query).__name__}"
            )

        params = {"rows": rows, "cursor": "*"}

        # query.bibliographic - string or list
        qb = query.get("query.bibliographic")
        if qb:
            if isinstance(qb, str):
                params["query.bibliographic"] = qb
            elif isinstance(qb, list):
                # urllib.urlencode with doseq=True will emit repeated params
                # for list values: query.bibliographic=A&query.bibliographic=B
                params["query.bibliographic"] = qb
            else:
                raise ValueError(
                    "crossref query.bibliographic must be string or list"
                )

        # query.title - narrower
        qt = query.get("query.title")
        if qt:
            if not isinstance(qt, (str, list)):
                raise ValueError("crossref query.title must be string or list")
            params["query.title"] = qt

        # query.author / query.affiliation / query.container-title - pass through
        for passthrough in ("query.author", "query.affiliation",
                            "query.container-title", "query.editor"):
            v = query.get(passthrough)
            if v:
                params[passthrough] = v

        if any(k.startswith("query") for k in params):
            params.update(by_score)

        # Filters (strict - actually exclude non-matching)
        user_filters = query.get("filter") or {}
        if not isinstance(user_filters, dict):
            raise ValueError("crossref `filter` must be a dict")

        merged_filters = {**user_filters, **engine_filters}  # engine wins on conflict
        if merged_filters:
            params["filter"] = ",".join(
                f"{k}:{v}" for k, v in merged_filters.items()
            )

        if self.contact_email:
            params["mailto"] = self.contact_email

        # Human-readable summary for logging
        parts = []
        if "query.bibliographic" in params:
            qb_val = params["query.bibliographic"]
            if isinstance(qb_val, list):
                parts.append(
                    f"query.bibliographic[{len(qb_val)} groups]: "
                    + " AND ".join(f"({g})" for g in qb_val)
                )
            else:
                parts.append(f"query.bibliographic: ({qb_val})")
        if "query.title" in params:
            parts.append(f"query.title: ({params['query.title']})")
        if "filter" in params:
            parts.append(f"filter: {params['filter']}")
        log_str = " | ".join(parts) or "(empty query)"

        return params, log_str

    def _to_record(self, item: dict) -> NormalizedRecord:
        authors = []
        for a in item.get("author", []) or []:
            authors.append({
                "family": a.get("family", ""),
                "given": a.get("given", "")
            })

        title_list = item.get("title") or []
        title = title_list[0] if title_list else ""

        year = None
        for k in ("published-print", "published-online", "issued", "created"):
            dp = (item.get(k) or {}).get("date-parts") or []
            if dp and dp[0]:
                year = dp[0][0]
                break

        venue = None
        ct = item.get("container-title") or []
        if ct:
            venue = ct[0]

        return NormalizedRecord(
            source="crossref",
            source_id=item.get("DOI", ""),
            title=_strip_tags(title) or "",
            abstract=_strip_tags(item.get("abstract")),
            authors=authors,
            year=year,
            doi=item.get("DOI"),
            venue=venue,
            document_type=item.get("type"),
            language=item.get("language"),
            url=item.get("URL"),
            native_relevance_score=item.get("score"),
            raw=item,
        )
