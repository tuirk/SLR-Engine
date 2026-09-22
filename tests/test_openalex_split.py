"""Long OpenAlex queries are split without breaking their Boolean structure."""
from slr_engine.sources.openalex import _split_query


def test_short_query_is_sent_unchanged():
    q = '("vibe coding" OR "vibe-coding") AND (LLM)'
    assert _split_query(q) == [q]


def test_long_query_is_split_inside_its_largest_or_group():
    a_terms = [f'"concept a term {i}"' for i in range(60)]
    tail = ' AND ("large language model" OR LLM) AND (empirical OR study)'
    q = "(" + " OR ".join(a_terms) + ")" + tail

    chunks = _split_query(q, max_chars=400)

    assert len(chunks) > 1
    seen = []
    for c in chunks:
        assert len(c) <= 400
        assert c.count("(") == c.count(")")
        assert c.endswith(tail)
        seen += c[: -len(tail)].strip("()").split(" OR ")
    assert seen == a_terms


def test_or_group_with_nested_parentheses_keeps_them_whole():
    terms = [f'("term {i}" AND variant{i})' for i in range(30)]
    q = "(" + " OR ".join(terms) + ") AND (study OR trial)"
    for c in _split_query(q, max_chars=300):
        assert c.count("(") == c.count(")")
        assert c.endswith(" AND (study OR trial)")


def test_query_without_an_or_group_is_sent_whole():
    q = " AND ".join(f'"term {i}"' for i in range(50))
    assert _split_query(q, max_chars=100) == [q]
