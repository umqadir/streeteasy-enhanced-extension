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
