from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cv_pipeline.paths import VolumePaths


@dataclass(frozen=True)
class Dust3RConfig:
    checkpoint_name: str = "DUSt3R_ViTLarge_BaseDecoder_512_dpt.pth"
    image_size: int = 512
    batch_size: int = 1
    niter: int = 300
    lr: float = 0.01
    schedule: str = "cosine"
    max_images: int = 12


@dataclass(frozen=True)
class Dust3RResult:
    # Per-image, aligned outputs (still up-to-scale).
    depthmaps: list[np.ndarray]  # (H,W) float32
    masks: list[np.ndarray]  # (H,W) bool
    cam2world: np.ndarray  # (N,4,4) float64
    intrinsics: np.ndarray  # (N,3,3) float64
    points_world: list[np.ndarray]  # (H,W,3) float32 (derived from depth + poses)
    diagnostics: dict[str, object]


def _ensure_vendor(volume: VolumePaths) -> Path:
    repo = volume.vendor_dir / "dust3r"
    if not repo.exists():
        raise FileNotFoundError(
            f"Missing vendor repo: {repo}. Run: `python cv-pipeline/scripts/download_models.py vendor-all`"
        )
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    return repo


def run_dust3r_reconstruction(
    *,
    images: list[Path],
    volume: VolumePaths,
    work_dir: Path,
    cfg: Dust3RConfig,
) -> Dust3RResult:
    """
    Runs DUSt3R + global alignment to produce per-image depth maps, poses, and a point cloud.

    This is intended as a fallback when COLMAP fails (unknown intrinsics, weak overlap, textureless).
    """
    _ensure_vendor(volume)

    ckpt = volume.checkpoints_dir / "dust3r" / cfg.checkpoint_name
    if not ckpt.exists():
        raise FileNotFoundError(
            f"Missing DUSt3R checkpoint at {ckpt}. Run: `python cv-pipeline/scripts/download_models.py dust3r`"
        )

    try:
        import torch
        from dust3r.cloud_opt import GlobalAlignerMode, global_aligner  # type: ignore
        from dust3r.image_pairs import make_pairs  # type: ignore
        from dust3r.inference import inference  # type: ignore
        from dust3r.model import AsymmetricCroCo3DStereo  # type: ignore
        from dust3r.utils.image import load_images  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "DUSt3R dependencies missing. In the pod: `cd cv-pipeline && uv sync --extra gpu --extra research` "
            "and ensure vendor repos + checkpoints are downloaded."
        ) from e

    if cfg.max_images and len(images) > int(cfg.max_images):
        images = images[: int(cfg.max_images)]

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if hasattr(torch.serialization, "add_safe_globals"):
        torch.serialization.add_safe_globals([argparse.Namespace])
    model = AsymmetricCroCo3DStereo.from_pretrained(str(ckpt)).to(device).eval()

    imgs = load_images([str(p) for p in images], size=int(cfg.image_size))
    pairs = make_pairs(imgs, scene_graph="complete", prefilter=None, symmetrize=True)

    output = inference(pairs, model, device, batch_size=int(cfg.batch_size), verbose=False)

    scene = global_aligner(output, device=device, mode=GlobalAlignerMode.PointCloudOptimizer)
    _loss = scene.compute_global_alignment(init="mst", niter=int(cfg.niter), schedule=cfg.schedule, lr=float(cfg.lr))

    depthmaps_t = scene.get_depthmaps()
    pts3d_t = scene.get_pts3d()
    masks_t = scene.get_masks()
    cam2world_t = scene.get_im_poses()
    intr_t = scene.get_intrinsics()

    depthmaps = [d.detach().float().cpu().numpy().astype(np.float32) for d in depthmaps_t]
    pts3d = [p.detach().float().cpu().numpy().astype(np.float32) for p in pts3d_t]
    masks = [m.detach().cpu().numpy().astype(bool) for m in masks_t]
    cam2world = cam2world_t.detach().float().cpu().numpy().astype(np.float64)
    intr = intr_t.detach().float().cpu().numpy().astype(np.float64)

    diag = {
        "n_images": int(len(images)),
        "image_size": int(cfg.image_size),
        "batch_size": int(cfg.batch_size),
        "niter": int(cfg.niter),
        "lr": float(cfg.lr),
        "schedule": cfg.schedule,
        "device": device,
        "checkpoint": str(ckpt),
    }
    return Dust3RResult(
        depthmaps=depthmaps,
        masks=masks,
        cam2world=cam2world,
        intrinsics=intr,
        points_world=pts3d,
        diagnostics=diag,
    )
