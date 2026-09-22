"""Crossref requests ask for relevance order whenever there is a query."""
from slr_engine.sources.crossref import CrossrefAdapter


def _params(query):
    params, _ = CrossrefAdapter()._build_params(
        query, date_from="2025-02-01", date_to=None, max_records=100,
    )
    return params


def test_structured_query_is_sorted_by_score():
    params = _params({"query.bibliographic": ["vibe coding"],
                      "filter": {"has-abstract": "true"}})
    assert params["sort"] == "score"
    assert params["order"] == "desc"
    assert params["cursor"] == "*"
    assert params["filter"] == "has-abstract:true,from-pub-date:2025-02-01"


def test_legacy_string_query_is_sorted_by_score():
    params = _params("vibe coding")
    assert (params["sort"], params["order"]) == ("score", "desc")


def test_filter_only_request_has_no_relevance_sort():
    params = _params({"filter": {"type": "journal-article"}})
    assert "sort" not in params
