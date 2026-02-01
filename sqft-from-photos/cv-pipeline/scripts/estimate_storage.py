#!/usr/bin/env python3

from __future__ import annotations

import json
import math
import urllib.request
from dataclasses import dataclass
from typing import Optional


def _bytes_to_gb(n: int) -> float:
    return n / (1024**3)


def _fmt_bytes(n: Optional[int]) -> str:
    if n is None:
        return "unknown"
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n/1024:.1f} KB"
    if n < 1024**3:
        return f"{n/1024**2:.1f} MB"
    return f"{n/1024**3:.2f} GB"


def _head_content_length(url: str) -> Optional[int]:
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            cl = r.headers.get("Content-Length")
            if cl:
                return int(cl)
            return None
    except Exception:
        # Some endpoints don't support HEAD; try a 1-byte range GET.
        try:
            req = urllib.request.Request(url)
            req.add_header("Range", "bytes=0-0")
            with urllib.request.urlopen(req, timeout=20) as r:
                cr = r.headers.get("Content-Range")  # bytes 0-0/12345
                if cr and "/" in cr:
                    total = cr.split("/")[-1]
                    if total.isdigit():
                        return int(total)
                cl = r.headers.get("Content-Length")
                if cl:
                    return int(cl)
                return None
        except Exception:
            return None


def _hf_used_storage(repo: str) -> Optional[int]:
    url = f"https://huggingface.co/api/models/{repo}"
    with urllib.request.urlopen(url, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    v = data.get("usedStorage")
    return int(v) if isinstance(v, int) else None


@dataclass(frozen=True)
class Artifact:
    name: str
    kind: str  # "hf" or "url"
    ref: str


def main() -> None:
    """
    Estimates how much *downloaded model data* you should expect for the “full plan” set.

    Notes:
    - For Hugging Face repos, uses `usedStorage` from the public model API.
    - For direct URLs, uses Content-Length or a 1-byte Range request.
    - This does NOT include: vendor repo clones, Python env, compiled extensions, datasets, or run artifacts.
    """

    artifacts: list[Artifact] = [
        # Default pipeline model
        Artifact(
            "DepthAnythingV2 metric (Hypersim Large repo)",
            "hf",
            "depth-anything/Depth-Anything-V2-Metric-Hypersim-Large",
        ),
        # Research plan add-ons
        Artifact("UniDepth v1 vitl14 repo", "hf", "lpiccinelli/unidepth-v1-vitl14"),
        Artifact("MoGe-2 vitl normal repo", "hf", "Ruicheng/moge-2-vitl-normal"),
        Artifact(
            "Metric3D vit_small checkpoint",
            "url",
            "https://huggingface.co/JUGGHM/Metric3D/resolve/main/metric_depth_vit_small_800k.pth",
        ),
        Artifact(
            "Metric3D vit_large checkpoint",
            "url",
            "https://huggingface.co/JUGGHM/Metric3D/resolve/main/metric_depth_vit_large_800k.pth",
        ),
        Artifact(
            "DUSt3R vitl 512 dpt checkpoint",
            "url",
            "https://download.europe.naverlabs.com/ComputerVision/DUSt3R/DUSt3R_ViTLarge_BaseDecoder_512_dpt.pth",
        ),
        Artifact(
            "MASt3R main checkpoint",
            "url",
            "https://download.europe.naverlabs.com/ComputerVision/MASt3R/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth",
        ),
        Artifact(
            "MASt3R retrieval trainingfree",
            "url",
            "https://download.europe.naverlabs.com/ComputerVision/MASt3R/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric_retrieval_trainingfree.pth",
        ),
        Artifact(
            "MASt3R retrieval codebook",
            "url",
            "https://download.europe.naverlabs.com/ComputerVision/MASt3R/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric_retrieval_codebook.pkl",
        ),
    ]

    rows = []
    total_known = 0
    for a in artifacts:
        if a.kind == "hf":
            size = _hf_used_storage(a.ref)
        else:
            size = _head_content_length(a.ref)
        if size is not None:
            total_known += size
        rows.append({"name": a.name, "kind": a.kind, "ref": a.ref, "bytes": size})

    # Conservative overhead multipliers for “real” disk usage.
    # These are intentionally *not* added into `total_known`.
    overhead = {
        "vendor_repos_estimate_gb": 5.0,
        "hf_cache_overhead_factor": 1.2,  # metadata, temp files, partial downloads
        "torch_cache_estimate_gb": 2.0,
    }

    report = {
        "total_known_bytes": total_known,
        "total_known_human": _fmt_bytes(total_known),
        "rows": rows,
        "overhead_rules_of_thumb": overhead,
        "suggested_volume_sizes_gb": {
            "models_only_min": math.ceil(_bytes_to_gb(total_known) * overhead["hf_cache_overhead_factor"] + 10),
            "models_plus_some_runs": 100,
            "models_plus_large_dataset_headroom": 200,
        },
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

