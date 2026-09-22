from slr_engine.oa_resolver import ALLOWED_OA_TIERS


def test_allowed_oa_tiers():
    assert "gold" in ALLOWED_OA_TIERS
    assert "diamond" in ALLOWED_OA_TIERS
    assert "hybrid" in ALLOWED_OA_TIERS
    assert "green" in ALLOWED_OA_TIERS
    assert "bronze" in ALLOWED_OA_TIERS
    assert "closed" not in ALLOWED_OA_TIERS
