from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np


class EmbeddingBackend(str, Enum):
    """
    Lightweight global image embedding backends for retrieval/overlap graphs.

    Notes:
    - These are used only to choose candidate image pairs for matching (SfM).
    - The goal is robustness and simplicity; accuracy improvements can come later.
    """

    TORCHVISION_RESNET50 = "torchvision-resnet50"
    TORCHVISION_VIT_B_16 = "torchvision-vit-b-16"
    DINOV2_VITS14 = "dinov2-vits14"


@dataclass(frozen=True)
class EmbeddingResult:
    image_names: list[str]
    embeddings: np.ndarray  # (N, D) float32 L2-normalized
    diagnostics: dict[str, object]


def _l2_normalize(x: np.ndarray) -> np.ndarray:
    denom = np.linalg.norm(x, axis=1, keepdims=True)
    denom = np.maximum(denom, 1e-12)
    return (x / denom).astype(np.float32)


def compute_image_embeddings(
    images: list[Path],
    *,
    backend: EmbeddingBackend,
    device: str = "cuda",
    batch_size: int = 8,
) -> EmbeddingResult:
    """
    Computes one global embedding per image.
    Returns L2-normalized embeddings for cosine similarity with dot products.
    """
    try:
        import torch
        import torchvision.transforms as T
    except Exception as e:  # pragma: no cover
        raise RuntimeError("Missing torch/torchvision. In the pod: `cd cv-pipeline && uv sync --extra gpu`.") from e

    from PIL import Image

    device_t = torch.device(device if torch.cuda.is_available() and device.startswith("cuda") else "cpu")

    if backend == EmbeddingBackend.TORCHVISION_RESNET50:
        from torchvision.models import ResNet50_Weights, resnet50

        weights = ResNet50_Weights.DEFAULT
        model = resnet50(weights=weights)
        model.fc = torch.nn.Identity()
        preprocess = weights.transforms()
        dim = 2048
    elif backend == EmbeddingBackend.TORCHVISION_VIT_B_16:
        from torchvision.models import ViT_B_16_Weights, vit_b_16

        weights = ViT_B_16_Weights.DEFAULT
        model = vit_b_16(weights=weights)
        model.heads = torch.nn.Identity()
        preprocess = weights.transforms()
        dim = 768
    elif backend == EmbeddingBackend.DINOV2_VITS14:
        # Uses torch.hub (facebookresearch/dinov2). This will download the repo + weights on first use.
        # If you want to prefetch: add a one-time warmup step on the pod before running large sweeps.
        try:
            model = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14")
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "Failed to load DINOv2 via torch.hub. "
                "This usually means the pod cannot reach GitHub to fetch the hub repo. "
                "Use a different embedding backend (e.g. torchvision-resnet50), or pre-install dinov2."
            ) from e
        model.head = torch.nn.Identity() if hasattr(model, "head") else model
        dim = 384
        preprocess = T.Compose(
            [
                T.Resize(224, interpolation=T.InterpolationMode.BICUBIC),
                T.CenterCrop(224),
                T.ToTensor(),
                T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ]
        )
    else:  # pragma: no cover
        raise ValueError(f"Unsupported embedding backend: {backend}")

    model = model.to(device_t).eval()

    names = [p.name for p in images]
    out = np.zeros((len(images), dim), dtype=np.float32)

    with torch.inference_mode():
        for i in range(0, len(images), batch_size):
            batch_paths = images[i : i + batch_size]
            batch = []
            for p in batch_paths:
                img = Image.open(p).convert("RGB")
                batch.append(preprocess(img))
            x = torch.stack(batch, dim=0).to(device_t)
            feats = model(x)
            if isinstance(feats, (tuple, list)):
                feats = feats[0]
            feats = feats.detach().float().cpu().numpy()
            out[i : i + len(batch_paths), :] = feats.astype(np.float32)

    out = _l2_normalize(out)
    return EmbeddingResult(
        image_names=names,
        embeddings=out,
        diagnostics={
            "backend": str(backend),
            "device": str(device_t),
            "batch_size": int(batch_size),
            "n_images": int(len(images)),
            "dim": int(dim),
        },
    )

