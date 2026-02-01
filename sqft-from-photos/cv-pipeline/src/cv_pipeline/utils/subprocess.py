from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CmdResult:
    cmd: list[str]
    returncode: int


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(
            f"Required binary not found on PATH: {name}. "
            f"Install it (RunPod: `bash cv-pipeline/scripts/runpod_setup_system.sh`)."
        )
    return path


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> CmdResult:
    stdout_f = open(stdout_path, "wb") if stdout_path else subprocess.DEVNULL  # noqa: SIM115
    stderr_f = open(stderr_path, "wb") if stderr_path else subprocess.DEVNULL  # noqa: SIM115
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=stdout_f,
            stderr=stderr_f,
            check=False,
        )
    finally:
        if stdout_path:
            stdout_f.close()
        if stderr_path:
            stderr_f.close()
    if p.returncode != 0:
        raise RuntimeError(f"Command failed ({p.returncode}): {' '.join(cmd)}")
    return CmdResult(cmd=cmd, returncode=p.returncode)

