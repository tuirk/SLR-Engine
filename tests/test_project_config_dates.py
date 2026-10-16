"""Unquoted YAML dates in project.yaml load as ISO strings."""
from slr_engine.store import ProjectConfig


def _load(tmp_path, dates):
    (tmp_path / "project.yaml").write_text(
        f"project_id: demo\ntopic: eggs\n{dates}", encoding="utf-8")
    return ProjectConfig.load(tmp_path)


def test_unquoted_dates_become_strings(tmp_path):
    cfg = _load(tmp_path, "date_from: 2021-01-01\ndate_to: 2024-12-31\n")
    assert cfg.date_from == "2021-01-01" and isinstance(cfg.date_from, str)
    assert cfg.date_to == "2024-12-31" and isinstance(cfg.date_to, str)


def test_quoted_and_missing_dates_are_unchanged(tmp_path):
    cfg = _load(tmp_path, "date_from: '2021-01-01'\ndate_to: null\n")
    assert cfg.date_from == "2021-01-01"
    assert cfg.date_to is None
