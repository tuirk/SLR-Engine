"""DBLP adapter."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Iterator, Optional

from . import NormalizedRecord, SourceAdapter


class DBLPAdapter(SourceAdapter):
    name = "dblp"
    base = "https://dblp.org/search/publ/api"

    def search(
        self,
        query: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        languages: Optional[list[str]] = None,
        max_records: int = 1000,
    ) -> Iterator[NormalizedRecord]:
        params = {"q": query, "format": "json", "h": str(min(max_records, 1000))}
        url = f"{self.base}?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            data = json.loads(self._fetch(req))
        except Exception as e:
            self._record_error(f"dblp request failed: {type(e).__name__}: {e} (url={url[:200]})")
            return

        hits = (((data.get("result") or {}).get("hits") or {}).get("hit") or [])
        for hit in hits[:max_records]:
            info = hit.get("info") or {}
            rec = self._to_record(info)
            if rec.title:
                yield rec

    def _to_record(self, info: dict) -> NormalizedRecord:
        authors_raw = (info.get("authors") or {}).get("author") or []
        if isinstance(authors_raw, dict):
            authors_raw = [authors_raw]
        elif isinstance(authors_raw, str):
            authors_raw = [{"text": authors_raw}]
        authors = []
        for a in authors_raw:
            name = a.get("text") if isinstance(a, dict) else str(a)
            parts = name.rsplit(" ", 1)
            authors.append({
                "family": parts[-1] if parts else "",
                "given": parts[0] if len(parts) == 2 else "",
            })
        doi = info.get("doi")
        return NormalizedRecord(
            source="dblp",
            source_id=info.get("key") or doi or f"dblp:{(info.get('title') or '')[:80]}",
            title=info.get("title") or "",
            abstract=None,
            authors=authors,
            year=int(info["year"]) if str(info.get("year", "")).isdigit() else None,
            doi=doi,
            venue=info.get("venue"),
            document_type=info.get("type"),
            url=info.get("url") or (f"https://doi.org/{doi}" if doi else None),
            raw=info,
        )
