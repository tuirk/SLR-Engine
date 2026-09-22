"""arXiv adapter.

Docs: https://info.arxiv.org/help/api/user-manual.html
The API returns Atom XML. We parse out title, abstract, authors, year,
arXiv ID, DOI (if the preprint has been linked to a published version),
primary subject category, and PDF URL.

Querying:
- Pagination via `start` and `max_results`. arXiv limits page size to 2000
  but recommends <= 1000 for stability; we use 200 per page.
- Date filtering via `submittedDate:[YYYYMMDDHHMM TO YYYYMMDDHHMM]`.
- Field-restricted search via prefixes: `ti:` (title), `abs:` (abstract),
  `all:` (default).
"""
from __future__ import annotations

import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Iterator, Optional

try:
    # export.arxiv.org rejects urllib's TLS handshake with HTTP 406 (seen
    # September 2026); requests gets through, so prefer it when installed.
    import requests
except ImportError:  # pragma: no cover - requests ships with markitdown
    requests = None

from . import SourceAdapter, NormalizedRecord


NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


class ArxivAdapter(SourceAdapter):
    name = "arxiv"
    base = "https://export.arxiv.org/api/query"

    def _get(self, params: dict) -> bytes:
        headers = {"User-Agent": self.user_agent}
        if requests is not None:
            resp = requests.get(self.base, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.content
        url = f"{self.base}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as resp:
            return resp.read()

    def search(
        self,
        query: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        languages: Optional[list[str]] = None,   # arXiv has no language filter; ignored
        max_records: int = 1000,
    ) -> Iterator[NormalizedRecord]:
        # Compose query. If user gave plain Boolean, wrap in `all:`.
        # If they used arXiv's prefix syntax (ti:, abs:, etc.), use as-is.
        if any(tok in query for tok in ("ti:", "abs:", "all:", "au:", "cat:")):
            search_query = query
        else:
            search_query = f"all:{query}"

        if date_from or date_to:
            lo = (date_from or "1991-01-01").replace("-", "") + "0000"
            hi = (date_to or "2099-12-31").replace("-", "") + "2359"
            search_query = f"({search_query}) AND submittedDate:[{lo} TO {hi}]"

        page_size = 200
        fetched = 0
        start = 0
        while fetched < max_records:
            n = min(page_size, max_records - fetched)
            params = {
                "search_query": search_query,
                "start": str(start),
                "max_results": str(n),
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
            url = f"{self.base}?{urllib.parse.urlencode(params)}"
            try:
                body = self._with_retry(lambda: self._get(params))
            except Exception as e:
                self._record_error(
                    f"arxiv request failed: {type(e).__name__}: {e} "
                    f"(url={url[:200]})"
                )
                return

            try:
                entries = list(ET.fromstring(body).findall("atom:entry", NS))
            except ET.ParseError as e:
                self._record_error(f"arxiv returned malformed XML: {e}")
                return
            if not entries:
                return
            for e in entries:
                yield self._parse_entry(e)
                fetched += 1
                if fetched >= max_records:
                    return
            start += n
            # arXiv requests >= 3s between calls
            time.sleep(3.0)

    def _parse_entry(self, e: ET.Element) -> NormalizedRecord:
        def text(path):
            n = e.find(path, NS)
            return (n.text or "").strip() if n is not None and n.text else None

        arxiv_url = text("atom:id")  # e.g. http://arxiv.org/abs/2401.12345v2
        arxiv_id = arxiv_url.rsplit("/", 1)[-1] if arxiv_url else ""
        title = (text("atom:title") or "").replace("\n", " ").strip()
        # arXiv squashes whitespace in titles; collapse
        title = " ".join(title.split())
        abstract = text("atom:summary")
        if abstract:
            abstract = " ".join(abstract.split())

        published = text("atom:published")  # 2024-01-15T18:00:00Z
        year = int(published[:4]) if published and published[:4].isdigit() else None

        authors = []
        for a in e.findall("atom:author", NS):
            name_el = a.find("atom:name", NS)
            if name_el is None or not name_el.text:
                continue
            name = name_el.text.strip()
            parts = name.rsplit(" ", 1)
            if len(parts) == 2:
                given, family = parts
            else:
                family, given = name, ""
            authors.append({"family": family, "given": given})

        # DOI link if arXiv lists one (set when the preprint maps to a publication)
        doi = None
        for link in e.findall("atom:link", NS):
            if link.get("title") == "doi":
                href = link.get("href") or ""
                # often https://doi.org/10.xxxx/yyyy
                if "doi.org/" in href:
                    doi = href.split("doi.org/", 1)[1]

        primary_cat = None
        pc = e.find("arxiv:primary_category", NS)
        if pc is not None:
            primary_cat = pc.get("term")

        pdf_url = None
        for link in e.findall("atom:link", NS):
            if link.get("type") == "application/pdf":
                pdf_url = link.get("href")
                break

        return NormalizedRecord(
            source="arxiv",
            source_id=arxiv_id,
            title=title,
            abstract=abstract,
            authors=authors,
            year=year,
            doi=doi,
            venue=primary_cat,                 # use category as "venue" surrogate
            document_type="preprint",
            language=None,                     # arXiv doesn't expose this
            keywords=[t.get("term") for t in e.findall("atom:category", NS)
                      if t.get("term")],
            url=pdf_url or arxiv_url,
            raw={"arxiv_id": arxiv_id, "primary_category": primary_cat},
        )
