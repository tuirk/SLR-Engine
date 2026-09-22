"""Crossref text-mining links count as open access only under an open license."""
import datetime

from slr_engine import oa_resolver
from slr_engine.oa_resolver import _collect_crossref_links, _open_license

TDM_LINK = {
    "URL": "https://api.elsevier.com/content/article/PII:S0000000000000000?httpAccept=text/xml",
    "content-type": "text/xml",
    "intended-application": "text-mining",
}
ELSEVIER_TDM_LICENSE = {"URL": "https://www.elsevier.com/tdm/userlicense/1.0/",
                        "start": {"date-parts": [[2025, 3, 1]]}}
CC_BY = {"URL": "http://creativecommons.org/licenses/by/4.0/",
         "start": {"date-parts": [[2025, 3, 1]]}}


def _crossref(monkeypatch, licenses):
    payload = {"message": {"license": licenses, "link": [TDM_LINK]}}
    monkeypatch.setattr(oa_resolver, "_get_json", lambda *a, **k: payload)
    return _collect_crossref_links(doi="10.1016/j.example.2025.100001")


def test_subscription_article_links_are_not_open_access(monkeypatch):
    assert _crossref(monkeypatch, [ELSEVIER_TDM_LICENSE]) == []


def test_cc_licensed_article_links_are_kept(monkeypatch):
    cands = _crossref(monkeypatch, [ELSEVIER_TDM_LICENSE, CC_BY])
    assert [c["file_format"] for c in cands] == ["xml"]
    assert cands[0]["license"] == CC_BY["URL"]


def test_embargoed_license_does_not_count_yet():
    later = {"URL": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
             "start": {"date-parts": [[2027, 1]]}}
    assert _open_license({"license": [later]}, today=datetime.date(2026, 9, 24)) is None
    assert _open_license({"license": [later]}, today=datetime.date(2027, 2, 1)) == later["URL"]


def test_malformed_license_date_does_not_crash():
    odd = {"URL": "http://creativecommons.org/licenses/by/4.0/",
           "start": {"date-parts": [[2025, 0, 0]]}}
    assert _open_license({"license": [odd]}) == odd["URL"]
