"""Europe PMC adapter.

Docs: https://europepmc.org/RestfulWebService
"""
from __future__ import annotations

import urllib.parse
import urllib.request
import json
from typing import Iterator, Optional

from . import SourceAdapter, NormalizedRecord


class EuropePMCAdapter(SourceAdapter):
    name = "europe_pmc"
    base = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

    def search(
        self,
        query: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        languages: Optional[list[str]] = None,
        max_records: int = 1000,
    ) -> Iterator[NormalizedRecord]:
        # Compose EPMC query string
        parts = [f"({query})"]
        if date_from or date_to:
            lo = date_from[:4] if date_from else "*"
            hi = date_to[:4] if date_to else "*"
            parts.append(f"PUB_YEAR:[{lo} TO {hi}]")
        if languages:
            lang_clause = " OR ".join(f'LANG:"{l}"' for l in languages)
            parts.append(f"({lang_clause})")
        full_q = " AND ".join(parts)

        params = {
            "query": full_q,
            "format": "json",
            "pageSize": "100",
            "resultType": "core",
            "cursorMark": "*",
        }

        fetched = 0
        while True:
            url = f"{self.base}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            try:
                data = json.loads(self._fetch(req))
            except Exception as e:
                self._record_error(
                    f"europe_pmc request failed: {type(e).__name__}: {e} "
                    f"(url={url[:200]})"
                )
                return

            results = data.get("resultList", {}).get("result", [])
            if not results:
                return

            for r in results:
                yield self._to_record(r)
                fetched += 1
                if fetched >= max_records:
                    return

            next_cursor = data.get("nextCursorMark")
            if not next_cursor or next_cursor == params["cursorMark"]:
                return
            params["cursorMark"] = next_cursor
            self._polite_sleep(0.1)

    def _to_record(self, r: dict) -> NormalizedRecord:
        authors = []
        for a in (r.get("authorList") or {}).get("author", []) or []:
            authors.append({
                "family": a.get("lastName", ""),
                "given": a.get("firstName") or a.get("initials") or "",
            })

        year = None
        y = r.get("pubYear")
        if y and str(y).isdigit():
            year = int(y)

        return NormalizedRecord(
            source="europe_pmc",
            source_id=f"{r.get('source', 'MED')}:{r.get('id', '')}",
            title=r.get("title", "").rstrip("."),
            abstract=r.get("abstractText"),
            authors=authors,
            year=year,
            doi=r.get("doi"),
            pmid=r.get("pmid"),
            pmcid=r.get("pmcid"),
            venue=r.get("journalTitle"),
            document_type=r.get("pubType"),
            language=r.get("language"),
            keywords=(r.get("keywordList") or {}).get("keyword"),
            url=f"https://europepmc.org/abstract/{r.get('source', 'MED')}/{r.get('id', '')}",
            raw=r,
        )
