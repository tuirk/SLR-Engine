"""PMC ids are compared and looked up in one form, "PMC1234567"."""
from urllib.parse import unquote_plus

from slr_engine import oa_resolver
from slr_engine.oa_resolver import _collect_europe_pmc, resolve_candidates
from slr_engine.store import normalize_pmcid


def test_normalize_pmcid():
    assert normalize_pmcid("12888021") == "PMC12888021"
    assert normalize_pmcid("PMC12888021") == "PMC12888021"
    assert normalize_pmcid("pmc12888021") == "PMC12888021"
    assert normalize_pmcid("https://www.ncbi.nlm.nih.gov/pmc/articles/12888021") == "PMC12888021"
    assert normalize_pmcid(None) is None
    assert normalize_pmcid("  ") is None


EPMC_HIT = {"resultList": {"result": [{
    "isOpenAccess": "Y", "license": "cc by", "id": "41492971", "source": "MED",
    "pmcid": "PMC12888021", "fullTextIdList": {"fullTextId": ["PMC12888021"]},
}]}}


def test_europe_pmc_is_queried_with_the_prefixed_id(monkeypatch):
    urls = []

    def fake(url, *a, **k):
        urls.append(unquote_plus(url))
        return EPMC_HIT

    monkeypatch.setattr(oa_resolver, "_get_json", fake)
    cands = _collect_europe_pmc(pmid=None, pmcid="12888021", doi=None)
    assert "PMCID:PMC12888021" in urls[0]
    assert {c["file_format"] for c in cands} == {"xml", "pdf"}


def test_full_text_xml_is_keyed_by_the_pmc_id(monkeypatch):
    monkeypatch.setattr(oa_resolver, "_get_json", lambda *a, **k: EPMC_HIT)
    xml = [c for c in _collect_europe_pmc(pmid="41492971", pmcid=None, doi=None)
           if c["file_format"] == "xml"]
    assert xml[0]["url"] == "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC12888021/fullTextXML"


def test_europe_pmc_api_xml_ranks_first(monkeypatch):
    monkeypatch.setattr(oa_resolver, "_get_json",
                        lambda url, *a, **k: EPMC_HIT if "ebi.ac.uk" in url else None)
    cands = resolve_candidates(pmcid="12888021")
    assert [(c["resolver_source"], c["file_format"]) for c in cands][:3] == [
        ("europepmc", "xml"), ("europepmc", "pdf"), ("pmc", "pdf"),
    ]
