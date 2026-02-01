#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Check:
    ok: bool
    name: str
    details: object


def _run(cmd: list[str], *, timeout_s: int = 5) -> tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def _check_binary(name: str, args: list[str]) -> Check:
    path = shutil.which(name)
    if not path:
        return Check(ok=False, name=f"binary:{name}", details="not found on PATH")
    rc, out, err = _run([path, *args], timeout_s=10)
    if rc != 0:
        return Check(ok=False, name=f"binary:{name}", details={"path": path, "stderr": err})
    return Check(ok=True, name=f"binary:{name}", details={"path": path, "stdout": out})


def _check_nvidia_smi() -> Check:
    path = shutil.which("nvidia-smi")
    if not path:
        return Check(ok=False, name="gpu:nvidia-smi", details="nvidia-smi not found (no NVIDIA driver?)")

    rc, out, err = _run(
        [
            path,
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        timeout_s=10,
    )
    if rc != 0:
        return Check(ok=False, name="gpu:nvidia-smi", details={"stderr": err})

    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        name, mem_mb, drv = parts
        gpus.append({"name": name, "memory_mb": int(mem_mb), "driver_version": drv})

    if not gpus:
        return Check(ok=False, name="gpu:nvidia-smi", details={"raw": out})

    return Check(ok=True, name="gpu:nvidia-smi", details={"gpus": gpus})


def main() -> None:
    report: dict[str, object] = {
        "python": sys.version,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "env": {
            "CVP_VOLUME": os.environ.get("CVP_VOLUME"),
            "CVP_WORKDIR": os.environ.get("CVP_WORKDIR"),
        },
        "checks": [],
    }

    checks = [
        _check_nvidia_smi(),
        _check_binary("colmap", ["-h"]),
        _check_binary("git", ["--version"]),
    ]

    report["checks"] = [check.__dict__ for check in checks]

    # Also print a minimal “recommendation” based on detected VRAM.
    vram_gb = None
    for c in checks:
        if c.name == "gpu:nvidia-smi" and c.ok:
            gpus = c.details.get("gpus", []) if isinstance(c.details, dict) else []
            if gpus:
                vram_gb = float(gpus[0]["memory_mb"]) / 1024.0
            break

    if vram_gb is None:
        report["recommendation"] = "No NVIDIA GPU detected; this project expects a CUDA GPU for model inference."
    elif vram_gb < 24:
        report["recommendation"] = (
            f"Detected ~{vram_gb:.1f} GB VRAM. This is likely too small for DUSt3R/MASt3R + large metric models; "
            "use >=24GB (min) or >=48GB (recommended)."
        )
    elif vram_gb < 48:
        report["recommendation"] = (
            f"Detected ~{vram_gb:.1f} GB VRAM. Should run most models sequentially with conservative settings; "
            "48GB+ is recommended for running the full research plan comfortably."
        )
    else:
        report["recommendation"] = f"Detected ~{vram_gb:.1f} GB VRAM. Good for the full research plan."

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

