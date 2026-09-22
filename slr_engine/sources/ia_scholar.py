"""Internet Archive Scholar adapter."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Iterator, Optional

from . import NormalizedRecord, SourceAdapter


class IAScholarAdapter(SourceAdapter):
    name = "ia_scholar"
    base = "https://scholar.archive.org/search"

    def search(
        self,
        query: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        languages: Optional[list[str]] = None,
        max_records: int = 1000,
    ) -> Iterator[NormalizedRecord]:
        params = {"q": query, "format": "json", "limit": str(min(max_records, 1000))}
        url = f"{self.base}?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            data = json.loads(self._fetch(req))
        except Exception as e:
            self._record_error(
                f"ia_scholar request failed: {type(e).__name__}: {e} (url={url[:200]})"
            )
            return

        rows = data.get("results") or data.get("response", {}).get("docs") or data.get("documents") or []
        for row in rows[:max_records]:
            rec = self._to_record(row)
            if rec.title:
                yield rec

    def _to_record(self, row: dict) -> NormalizedRecord:
        title = row.get("title")
        if isinstance(title, list):
            title = title[0] if title else ""
        authors_raw = row.get("authors") or row.get("creator") or row.get("author") or []
        if isinstance(authors_raw, str):
            authors_raw = [authors_raw]
        authors = []
        for name in authors_raw:
            parts = str(name).rsplit(" ", 1)
            authors.append({
                "family": parts[-1] if parts else "",
                "given": parts[0] if len(parts) == 2 else "",
            })
        year = row.get("year") or row.get("date")
        year = int(str(year)[:4]) if str(year)[:4].isdigit() else None
        doi = row.get("doi")
        if isinstance(doi, list):
            doi = doi[0] if doi else None
        ia_id = row.get("ia_id") or row.get("identifier") or row.get("id")
        return NormalizedRecord(
            source="ia_scholar",
            source_id=ia_id or doi or f"ia:{(title or '')[:80]}",
            title=title or "",
            abstract=row.get("abstract") or row.get("description"),
            authors=authors,
            year=year,
            doi=doi,
            venue=row.get("venue") or row.get("journal"),
            document_type=row.get("type"),
            url=row.get("url") or (f"https://archive.org/details/{ia_id}" if ia_id else None),
            raw=row,
        )
