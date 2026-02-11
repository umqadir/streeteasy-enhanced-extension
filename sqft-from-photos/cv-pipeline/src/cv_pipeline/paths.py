from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VolumePaths:
    root: Path

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def checkpoints_dir(self) -> Path:
        return self.models_dir / "checkpoints"

    @property
    def vendor_dir(self) -> Path:
        return self.models_dir / "vendor"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"


@dataclass(frozen=True)
class WorkPaths:
    root: Path


def default_volume_root() -> Path:
    env = os.environ.get("CVP_VOLUME")
    if env:
        return Path(env)
    for candidate in ("/runpod-volume", "/workspace"):
        p = Path(candidate)
        if p.exists():
            return p
    return Path.home() / ".cache" / "cv_pipeline"


def default_work_root() -> Path:
    env = os.environ.get("CVP_WORKDIR")
    if env:
        return Path(env)
    return Path("/tmp/cv_pipeline_work")


def ensure_dirs(*paths: Path) -> None:
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)


def setup_model_caches(volume: VolumePaths) -> None:
    """
    Make "download once, reuse across runs" caches land on the network volume.
    Only sets env vars if they're not already set.
    """
    models = volume.models_dir
    ensure_dirs(models)

    os.environ.setdefault("TORCH_HOME", str(models / "torch"))
    os.environ.setdefault("HF_HOME", str(models / "hf"))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(models / "hf" / "transformers"))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(models / "hf" / "hub"))
    os.environ.setdefault("XDG_CACHE_HOME", str(volume.root / ".cache"))
    os.environ.setdefault("MPLCONFIGDIR", str(volume.root / ".cache" / "matplotlib"))


def _repo_root() -> Path:
    # .../cv-pipeline/src/cv_pipeline/paths.py -> repo root at parents[3]
    return Path(__file__).resolve().parents[3]


def default_streeteasy_dataset_path() -> Path | None:
    """
    Resolve the default StreetEasy dataset path for CLI commands.

    Priority:
    1) CVP_STREETEASY_DATASET env var
    2) sample-collection/clean_set_export/listings.json
    3) sample-collection/streeteasy_eval_dataset/listings.json
    """
    env = os.environ.get("CVP_STREETEASY_DATASET")
    if env:
        p = Path(env).expanduser().resolve()
        if p.exists():
            return p

    repo_root = _repo_root()
    sample_root = repo_root / "sample-collection"

    for candidate in (
        sample_root / "clean_set_export" / "listings.json",
        sample_root / "streeteasy_eval_dataset" / "listings.json",
    ):
        if candidate.exists():
            return candidate.resolve()

    return None
