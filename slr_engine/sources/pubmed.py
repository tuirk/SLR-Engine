"""PubMed adapter (NCBI E-utilities).

Docs: https://www.ncbi.nlm.nih.gov/books/NBK25499/
Flow: esearch → batched efetch.
"""
from __future__ import annotations

import urllib.parse
import urllib.request
import json
import xml.etree.ElementTree as ET
from typing import Iterator, Optional

from . import SourceAdapter, NormalizedRecord


class PubMedAdapter(SourceAdapter):
    name = "pubmed"
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(self, contact_email: Optional[str] = None,
                 api_key: Optional[str] = None,
                 user_agent: str = "slr-engine/1.0 (research; OA only)"):
        super().__init__(contact_email=contact_email, user_agent=user_agent)
        self.api_key = api_key

    def search(
        self,
        query: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        languages: Optional[list[str]] = None,
        max_records: int = 1000,
    ) -> Iterator[NormalizedRecord]:
        # 1. esearch to get PMIDs
        pmids = list(self._esearch(query, date_from, date_to, max_records))
        if not pmids:
            return

        # 2. efetch in batches of 200
        for i in range(0, len(pmids), 200):
            batch = pmids[i:i+200]
            for rec in self._efetch(batch):
                if languages and rec.language and rec.language not in languages:
                    continue
                yield rec
            self._polite_sleep(0.34)  # NCBI rate limit: 3/sec without key

    def _esearch(self, query: str, date_from: Optional[str],
                 date_to: Optional[str], max_records: int) -> Iterator[str]:
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": str(min(max_records, 10000)),
            "retmode": "json",
            "usehistory": "n",
        }
        if date_from:
            params["mindate"] = date_from.replace("-", "/")
            params["datetype"] = "pdat"
        if date_to:
            params["maxdate"] = date_to.replace("-", "/")
            params["datetype"] = "pdat"
        if self.api_key:
            params["api_key"] = self.api_key
        if self.contact_email:
            params["email"] = self.contact_email
            params["tool"] = "slr-engine"

        url = f"{self.base}/esearch.fcgi?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            data = json.loads(self._fetch(req))
        except Exception as e:
            self._record_error(
                f"pubmed esearch failed: {type(e).__name__}: {e} "
                f"(url={url[:200]})"
            )
            return
        for pmid in data.get("esearchresult", {}).get("idlist", []):
            yield pmid

    def _efetch(self, pmids: list[str]) -> Iterator[NormalizedRecord]:
        params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
        }
        if self.api_key:
            params["api_key"] = self.api_key
        if self.contact_email:
            params["email"] = self.contact_email
            params["tool"] = "slr-engine"

        url = f"{self.base}/efetch.fcgi?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            xml_data = self._fetch(req, timeout=60)
        except Exception as e:
            self._record_error(
                f"pubmed efetch failed: {type(e).__name__}: {e} "
                f"(url={url[:200]})"
            )
            return

        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError as e:
            self._record_error(f"pubmed efetch returned malformed XML: {e}")
            return
        for art in root.findall(".//PubmedArticle"):
            yield self._parse_article(art)

    def _parse_article(self, art: ET.Element) -> NormalizedRecord:
        def text(el, path):
            n = el.find(path)
            return n.text if n is not None and n.text else None

        pmid = text(art, ".//PMID")
        title = text(art, ".//ArticleTitle") or ""

        # Abstract may have multiple labeled sections
        abstract_parts = []
        for ab in art.findall(".//Abstract/AbstractText"):
            label = ab.get("Label")
            txt = "".join(ab.itertext()).strip()
            if txt:
                abstract_parts.append(f"{label}: {txt}" if label else txt)
        abstract = "\n".join(abstract_parts) or None

        authors = []
        for a in art.findall(".//Author"):
            family = text(a, "LastName") or ""
            given = text(a, "ForeName") or text(a, "Initials") or ""
            if family or given:
                authors.append({"family": family, "given": given})

        year = None
        y = text(art, ".//PubDate/Year")
        if y and y.isdigit():
            year = int(y)
        else:
            md = text(art, ".//PubDate/MedlineDate")
            if md and md[:4].isdigit():
                year = int(md[:4])

        venue = text(art, ".//Journal/Title")
        language = text(art, ".//Language")
        doi = None
        pmcid = None
        for el in art.findall(".//ArticleId"):
            if el.get("IdType") == "doi":
                doi = el.text
            elif el.get("IdType") == "pmc":
                pmcid = el.text

        ptypes = [p.text for p in art.findall(".//PublicationType") if p.text]
        keywords = [k.text for k in art.findall(".//Keyword") if k.text]

        return NormalizedRecord(
            source="pubmed",
            source_id=pmid or "",
            title=title,
            abstract=abstract,
            authors=authors,
            year=year,
            doi=doi,
            pmid=pmid,
            pmcid=pmcid,
            venue=venue,
            document_type=ptypes[0] if ptypes else None,
            language=language,
            keywords=keywords or None,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
            raw={"pmid": pmid, "publication_types": ptypes},
        )
