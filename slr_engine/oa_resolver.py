"""Open-access full-text resolver.

For each record, collect ranked OA URL candidates from lawful sources.
Refuses anything that isn't lawfully obtainable.

Candidate sources (order / ranking priority):
  1. Europe PMC (XML + PDF when OA)
  2. PMC (via PMCID)
  3. OpenAlex OA locations (best + all oa_locations)
  4. Unpaywall OA locations (published → accepted/AAM → submitted)
  5. arXiv PDF (from arXiv id or record URL)
  6. CORE (requires API key)
  7. Crossref text-mining links

`resolve()` returns the best candidate or None.
`resolve_candidates()` returns the full ranked, deduped list.
"""
from __future__ import annotations

import datetime
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

USER_AGENT = "slr-engine/1.0 (research; OA only)"

# OA tiers we will attempt to download; closed works are skipped at resolve
# and download time. Hybrid means openly licensed in a subscription journal
# and diamond is OpenAlex's name for gold without author fees: both are free
# to read and reuse.
ALLOWED_OA_TIERS = frozenset({"gold", "diamond", "hybrid", "green", "bronze"})

# New-style (2401.12345) or old-style (hep-th/9901001) id, version dropped.
_ARXIV_ID = r"(\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?"
# An id is only trusted where arXiv put it. Digit runs inside other
# identifiers look like ids but are not: 10.1016/j.nedt.2026.107278 holds
# "2026.10727", and repository links like /handle/123456789/64653 hold
# "handle/1234567".
_ARXIV_DOI_RE = re.compile(rf"10\.48550/arxiv\.{_ARXIV_ID}", re.IGNORECASE)
_ARXIV_URL_RE = re.compile(rf"arxiv\.org/(?:abs|pdf|html)/{_ARXIV_ID}", re.IGNORECASE)
_ARXIV_BARE_RE = re.compile(rf"^(?:arxiv:\s*)?{_ARXIV_ID}$", re.IGNORECASE)

_VERSION_RANK = {
    "publishedversion": 0,
    "acceptedversion": 1,
    "submittedversion": 2,
}

_SOURCE_RANK = {
    # Europe PMC serves the PMC open-access subset to scripts; the PMC site
    # itself now answers scripted requests with a reCAPTCHA page.
    "europepmc": 0,
    "pmc": 1,
    "openalex": 2,
    "unpaywall": 3,
    "arxiv": 4,
    "core": 5,
    "crossref": 6,
    "openalex_landing": 7,
    "unpaywall_landing": 8,
}

# Article landing pages of open-access works that have no direct file link.
# They keep the work's open-access status and give not_downloaded.csv a link
# to open by hand; stage 06 does not fetch them (see LANDING_REASON there).
LANDING_SOURCES = frozenset({"openalex_landing", "unpaywall_landing"})

_FORMAT_RANK = {"pdf": 0, "xml": 1, "html": 2}


def resolve(
    *,
    doi: Optional[str] = None,
    pmid: Optional[str] = None,
    pmcid: Optional[str] = None,
    openalex_id: Optional[str] = None,
    contact_email: Optional[str] = None,
    core_api_key: Optional[str] = None,
    openalex_api_key: Optional[str] = None,
    record_url: Optional[str] = None,
    source: Optional[str] = None,
    source_id: Optional[str] = None,
) -> Optional[dict]:
    """Return the best OA candidate, or None."""
    cands = resolve_candidates(
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        openalex_id=openalex_id,
        contact_email=contact_email,
        core_api_key=core_api_key,
        openalex_api_key=openalex_api_key,
        record_url=record_url,
        source=source,
        source_id=source_id,
    )
    return cands[0] if cands else None


def resolve_candidates(
    *,
    doi: Optional[str] = None,
    pmid: Optional[str] = None,
    pmcid: Optional[str] = None,
    openalex_id: Optional[str] = None,
    contact_email: Optional[str] = None,
    core_api_key: Optional[str] = None,
    openalex_api_key: Optional[str] = None,
    record_url: Optional[str] = None,
    source: Optional[str] = None,
    source_id: Optional[str] = None,
    errors: Optional[list] = None,
) -> list[dict]:
    """Collect ranked, URL-deduped OA download candidates.

    Lookups that fail (see LookupFailed) contribute no candidates and are
    appended to ``errors`` when a list is passed, so callers can tell "no
    open copy" apart from "could not check".
    """
    raw: list[dict] = []

    def attempt(name: str, collect: Callable[[], list]) -> list:
        try:
            return collect()
        except LookupFailed as e:
            if errors is not None:
                errors.append(f"{name}: {e}")
            return []

    # 1. PMC
    pmcid = _pmc_id(pmcid)
    if pmcid:
        raw.append({
            "resolver_source": "pmc",
            "url": f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/",
            "license": "pmc-oa",
            "file_format": "pdf",
            "oa_status": "gold",
            "version_rank": 0,
        })

    # 2. Europe PMC
    if pmid or pmcid or doi:
        raw.extend(attempt("europepmc", lambda: _collect_europe_pmc(
            pmid=pmid, pmcid=pmcid, doi=doi,
        )))

    # 3. OpenAlex
    if openalex_id or doi:
        raw.extend(attempt("openalex", lambda: _collect_openalex(
            openalex_id=openalex_id,
            doi=doi,
            contact_email=contact_email,
            api_key=openalex_api_key,
        )))

    # 4. Unpaywall
    if doi and contact_email:
        raw.extend(attempt("unpaywall", lambda: _collect_unpaywall(
            doi=doi, email=contact_email,
        )))

    # 5. arXiv
    raw.extend(_collect_arxiv(
        source=source, source_id=source_id, record_url=record_url, doi=doi,
    ))

    # 6. CORE
    if (doi or pmid) and core_api_key:
        core = attempt("core", lambda: [
            c for c in [_try_core(doi=doi, pmid=pmid, api_key=core_api_key)] if c
        ])
        for c in core:
            c["version_rank"] = 0
            raw.append(c)

    # 7. Crossref text-mining
    if doi:
        raw.extend(attempt("crossref", lambda: _collect_crossref_links(doi=doi)))

    return _rank_and_dedupe(raw)


def extract_arxiv_id(
    *,
    source: Optional[str] = None,
    source_id: Optional[str] = None,
    record_url: Optional[str] = None,
    doi: Optional[str] = None,
) -> Optional[str]:
    """arXiv id from a bare id, an arxiv.org URL or an arXiv DOI (10.48550)."""
    if source and str(source).lower() == "arxiv" and source_id:
        cleaned = _normalize_arxiv_id(str(source_id))
        if cleaned:
            return cleaned

    for blob in (source_id, record_url, doi):
        if not blob:
            continue
        cleaned = _normalize_arxiv_id(str(blob))
        if cleaned:
            return cleaned
    return None


def arxiv_pdf_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"


def _normalize_arxiv_id(text: str) -> Optional[str]:
    text = text.strip()
    for pattern in (_ARXIV_DOI_RE, _ARXIV_URL_RE, _ARXIV_BARE_RE):
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


_OSF_DOWNLOAD_RE = re.compile(
    r"^https?://osf\.io/([a-z0-9]{5,}(?:_v\d+)?)/download/?$", re.IGNORECASE
)


def direct_download_url(url: str) -> str:
    """Rewrite download links known to serve a web app instead of the file.

    OSF's current site answers https://osf.io/<id>_v1/download (the form
    OpenAlex reports for OSF preprints) with its JavaScript app, while
    https://osf.io/download/<id>/ redirects to the file itself.
    """
    m = _OSF_DOWNLOAD_RE.match(url.strip())
    if m:
        return f"https://osf.io/download/{m.group(1)}/"
    return url


def _normalize_url(url: str) -> str:
    u = (url or "").strip()
    # Strip fragments and trailing slashes for dedupe
    u = u.split("#", 1)[0].rstrip("/")
    return u.lower()


def _file_format_from_url(url: str, default: str = "html") -> str:
    low = url.lower().split("?", 1)[0]
    if low.endswith(".pdf") or "/pdf" in low or low.endswith("/pdf/"):
        return "pdf"
    if low.endswith(".xml") or "fulltextxml" in low:
        return "xml"
    return default


def _rank_and_dedupe(candidates: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for c in candidates:
        url = direct_download_url((c.get("url") or "").strip())
        if not url:
            continue
        key = _normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        item = dict(c)
        item["url"] = url
        if not item.get("file_format"):
            item["file_format"] = _file_format_from_url(url)
        item.setdefault("version_rank", 9)
        out.append(item)

    out.sort(key=lambda c: (
        _SOURCE_RANK.get(c.get("resolver_source", ""), 99),
        int(c.get("version_rank", 9)),
        _FORMAT_RANK.get(c.get("file_format", "html"), 9),
    ))
    # Drop ranking helper from public dicts (keep optional fields clean)
    for c in out:
        c.pop("version_rank", None)
    return out


class LookupFailed(Exception):
    """A resolver service could not be reached or kept failing, so its
    answer is unknown (as opposed to "this work has no open copy")."""


_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

# Lookups that failed in a row, per host. Past _HOST_DOWN_AFTER the host is
# treated as down for the rest of the run, so an outage costs one retry cycle
# per host instead of one per record.
_HOST_FAILURES: dict[str, int] = {}
_HOST_DOWN_AFTER = 3


def _get_json(
    url: str,
    headers: Optional[dict] = None,
    timeout: int = 30,
    data: Optional[bytes] = None,
    max_retries: int = 3,
) -> Optional[dict]:
    """Fetch JSON. None when the service has nothing for this work (a 4xx
    such as 404); LookupFailed when rate limits, server errors or network
    trouble persist after retries."""
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    host = urllib.parse.urlsplit(url).netloc
    if _HOST_FAILURES.get(host, 0) >= _HOST_DOWN_AFTER:
        raise LookupFailed(f"{host}: unavailable earlier in this run")
    attempt = 0
    while True:
        req = urllib.request.Request(url, headers=h, data=data)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read())
            _HOST_FAILURES.pop(host, None)
            return result
        except urllib.error.HTTPError as e:
            if e.code not in _RETRY_STATUSES:
                _HOST_FAILURES.pop(host, None)
                return None
            reason = f"HTTP {e.code}"
            retry_after = (e.headers or {}).get("Retry-After")
        except (OSError, ValueError) as e:
            reason = f"{type(e).__name__}: {e}"
            retry_after = None
        wait = 2.0 ** (attempt + 1)
        if retry_after:
            try:
                wait = float(retry_after)
            except ValueError:
                pass
        if attempt >= max_retries or wait > 60:
            _HOST_FAILURES[host] = _HOST_FAILURES.get(host, 0) + 1
            raise LookupFailed(f"{host}: {reason}")
        attempt += 1
        time.sleep(wait)


def _pmc_id(pmcid: Optional[str]) -> Optional[str]:
    """"PMC1234567" from a prefixed or bare PMC id (Europe PMC matches only
    the prefixed form)."""
    if not pmcid:
        return None
    s = str(pmcid).strip().rsplit("/", 1)[-1].upper()
    s = s[3:] if s.startswith("PMC") else s
    return f"PMC{s}" if s else None


def _collect_europe_pmc(*, pmid: Optional[str], pmcid: Optional[str],
                        doi: Optional[str]) -> list[dict]:
    pmcid = _pmc_id(pmcid)
    if pmcid:
        q = f"PMCID:{pmcid}"
    elif pmid:
        q = f"EXT_ID:{pmid} AND SRC:MED"
    elif doi:
        q = f'DOI:"{doi}"'
    else:
        return []

    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
           + urllib.parse.urlencode({
               "query": q, "format": "json", "resultType": "core",
           }))
    data = _get_json(url)
    if not data:
        return []
    results = data.get("resultList", {}).get("result", [])
    if not results:
        return []
    r = results[0]
    if r.get("isOpenAccess") != "Y":
        return []

    out: list[dict] = []
    license_ = r.get("license") or "epmc-oa"
    pmc = _pmc_id(r.get("pmcid") or pmcid)
    # fullTextXML is keyed by PMC id (or PPR id for preprints); the
    # source/record-id form (MED/<pmid>) returns 404.
    fulltext_id = pmc or (r.get("id") if r.get("source") == "PPR" else None)

    if r.get("fullTextIdList") and fulltext_id:
        out.append({
            "resolver_source": "europepmc",
            "url": (
                f"https://www.ebi.ac.uk/europepmc/webservices/rest/"
                f"{fulltext_id}/fullTextXML"
            ),
            "license": license_,
            "file_format": "xml",
            "oa_status": "gold",
            "version_rank": 0,
        })

    if pmc:
        out.append({
            "resolver_source": "europepmc",
            "url": f"https://europepmc.org/articles/{pmc}?pdf=render",
            "license": license_,
            "file_format": "pdf",
            "oa_status": "gold",
            # The website's render endpoint often refuses scripts (403), so
            # the API's XML above is tried first.
            "version_rank": 1,
        })
    return out


def _collect_openalex(
    *,
    openalex_id: Optional[str],
    doi: Optional[str],
    contact_email: Optional[str],
    api_key: Optional[str] = None,
) -> list[dict]:
    if openalex_id:
        wid = openalex_id.rsplit("/", 1)[-1]
        url = f"https://api.openalex.org/works/{wid}"
    elif doi:
        url = f"https://api.openalex.org/works/doi:{doi}"
    else:
        return []
    if contact_email:
        url += ("&" if "?" in url else "?") + f"mailto={contact_email}"

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = _get_json(url, headers=headers or None)
    if not data:
        return []

    oa_status = (data.get("open_access") or {}).get("oa_status") or "unknown"
    if oa_status not in ALLOWED_OA_TIERS:
        return []

    locations = []
    best = data.get("best_oa_location")
    if best:
        locations.append(best)
    for loc in data.get("oa_locations") or []:
        locations.append(loc)

    out: list[dict] = []
    for loc in locations:
        if not loc or not loc.get("is_oa"):
            continue
        pdf = loc.get("pdf_url")
        landing = loc.get("landing_page_url")
        if not (pdf or landing):
            continue
        out.append({
            "resolver_source": "openalex" if pdf else "openalex_landing",
            "url": pdf or landing,
            "license": loc.get("license"),
            "file_format": _file_format_from_url(pdf) if pdf else "html",
            "oa_status": oa_status,
            "version_rank": _VERSION_RANK.get(
                str(loc.get("version") or "").lower().replace("_", ""), 9
            ),
        })
    return out


def _collect_unpaywall(*, doi: str, email: str) -> list[dict]:
    url = (
        f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}"
        f"?email={urllib.parse.quote(email)}"
    )
    data = _get_json(url)
    if not data:
        return []

    oa_status = data.get("oa_status") or "unknown"
    if oa_status not in ALLOWED_OA_TIERS:
        return []

    locations = []
    best = data.get("best_oa_location")
    if best:
        locations.append(best)
    for loc in data.get("oa_locations") or []:
        locations.append(loc)

    out: list[dict] = []
    for loc in locations:
        if not loc:
            continue
        pdf = loc.get("url_for_pdf")
        landing = loc.get("url")
        if not (pdf or landing):
            continue
        out.append({
            "resolver_source": "unpaywall" if pdf else "unpaywall_landing",
            "url": pdf or landing,
            "license": loc.get("license"),
            "file_format": _file_format_from_url(pdf) if pdf else "html",
            "oa_status": oa_status,
            "version_rank": _VERSION_RANK.get(
                str(loc.get("version") or "").lower().replace("_", ""), 9
            ),
        })
    return out


def _collect_arxiv(
    *,
    source: Optional[str],
    source_id: Optional[str],
    record_url: Optional[str],
    doi: Optional[str],
) -> list[dict]:
    arxiv_id = extract_arxiv_id(
        source=source, source_id=source_id, record_url=record_url, doi=doi,
    )
    if not arxiv_id:
        return []
    return [{
        "resolver_source": "arxiv",
        "url": arxiv_pdf_url(arxiv_id),
        "license": "arxiv",
        "file_format": "pdf",
        "oa_status": "green",
        "version_rank": 1,
    }]


def _try_core(*, doi: Optional[str], pmid: Optional[str],
              api_key: str) -> Optional[dict]:
    q = f'doi:"{doi}"' if doi else f'pmid:"{pmid}"'
    data = _get_json(
        "https://api.core.ac.uk/v3/search/works",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        data=json.dumps({"q": q, "limit": 1}).encode(),
    )
    if not data:
        return None
    results = data.get("results") or []
    if not results:
        return None
    r = results[0]
    pdf = r.get("downloadUrl")
    if not pdf:
        return None
    return {
        "resolver_source": "core",
        "url": pdf,
        "license": (r.get("license") or {}).get("name"),
        "file_format": "pdf",
        "oa_status": "gold",
    }


def _open_license(msg: dict, today: Optional[datetime.date] = None) -> Optional[str]:
    """URL of a Creative Commons license already in effect for the work."""
    today = today or datetime.date.today()
    for lic in msg.get("license") or []:
        url = lic.get("URL") or ""
        if "creativecommons.org" not in url.lower():
            continue
        parts = ((lic.get("start") or {}).get("date-parts") or [[]])[0]
        try:
            start = datetime.date(*(list(parts) + [1, 1])[:3]) if parts and parts[0] else None
        except (TypeError, ValueError):
            start = None          # malformed date: judge by the license alone
        if start and start > today:
            continue              # embargoed: open later, not yet
        return url
    return None


def _collect_crossref_links(*, doi: str) -> list[dict]:
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
    data = _get_json(url)
    if not data:
        return []
    msg = data.get("message", {})
    # Publishers list text-mining links for subscription content too; only an
    # open license makes the linked full text open access.
    license_ = _open_license(msg)
    if not license_:
        return []
    out: list[dict] = []
    for link in msg.get("link", []) or []:
        if link.get("intended-application") != "text-mining":
            continue
        link_url = link.get("URL")
        if not link_url:
            continue
        ct = link.get("content-type", "")
        if "pdf" in ct:
            fmt = "pdf"
        elif "xml" in ct:
            fmt = "xml"
        else:
            fmt = _file_format_from_url(link_url)
        out.append({
            "resolver_source": "crossref",
            "url": link_url,
            "license": license_,
            "file_format": fmt,
            "oa_status": "gold",
            "version_rank": 0,
        })
    return out
