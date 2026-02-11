from pathlib import Path

from cv_pipeline.paths import default_streeteasy_dataset_path


def test_default_dataset_uses_env_override(monkeypatch, tmp_path: Path):
    ds = tmp_path / "dataset.json"
    ds.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CVP_STREETEASY_DATASET", str(ds))
    assert default_streeteasy_dataset_path() == ds.resolve()


def test_default_dataset_falls_back_to_clean_export(monkeypatch, tmp_path: Path):
    sample = tmp_path / "sample-collection"
    export = sample / "clean_set_export" / "listings.json"
    export.parent.mkdir(parents=True, exist_ok=True)
    export.write_text("{}", encoding="utf-8")

    monkeypatch.delenv("CVP_STREETEASY_DATASET", raising=False)
    monkeypatch.setattr("cv_pipeline.paths._repo_root", lambda: tmp_path)
    assert default_streeteasy_dataset_path() == export.resolve()


def test_default_dataset_falls_back_to_canonical_source(monkeypatch, tmp_path: Path):
    sample = tmp_path / "sample-collection"
    canonical = sample / "streeteasy_eval_dataset" / "listings.json"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("{}", encoding="utf-8")

    monkeypatch.delenv("CVP_STREETEASY_DATASET", raising=False)
    monkeypatch.setattr("cv_pipeline.paths._repo_root", lambda: tmp_path)
    assert default_streeteasy_dataset_path() == canonical.resolve()
