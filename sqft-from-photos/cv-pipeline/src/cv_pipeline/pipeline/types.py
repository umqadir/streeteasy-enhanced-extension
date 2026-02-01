from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ListingInputs:
    listing_id: str
    images_dir: Path
    label_sqft: float | None = None


@dataclass(frozen=True)
class RunArtifacts:
    run_id: str
    work_dir: Path
    volume_dir: Path
    images_dir: Path
    preprocessed_dir: Path
    colmap_dir: Path
    depth_dir: Path

