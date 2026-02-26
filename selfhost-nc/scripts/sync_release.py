#!/usr/bin/env python3
from __future__ import annotations

import shutil
from pathlib import Path


def copytree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns('.DS_Store', '__pycache__', '*.pyc'))


def main() -> None:
    script_path = Path(__file__).resolve()
    selfhost_root = script_path.parents[1]
    repo_root = selfhost_root.parents[0]

    src_extension = repo_root / 'extension'
    src_backend = repo_root / 'sqft-from-photos' / 'backend-local' / 'local_backend.py'
    src_v2 = repo_root / 'sqft-from-photos' / 'v2-pipeline' / 'estimate_v2b.py'

    dst_extension = selfhost_root / 'extension'
    dst_backend = selfhost_root / 'backend' / 'local_backend.py'
    dst_v2 = selfhost_root / 'v2-pipeline' / 'estimate_v2b.py'

    if not src_extension.is_dir():
        raise SystemExit(f'Missing source extension dir: {src_extension}')
    if not src_backend.is_file():
        raise SystemExit(f'Missing source backend file: {src_backend}')
    if not src_v2.is_file():
        raise SystemExit(f'Missing source pipeline file: {src_v2}')

    copytree(src_extension, dst_extension)
    dst_backend.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_backend, dst_backend)
    dst_v2.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_v2, dst_v2)

    print('Synced selfhost release assets:')
    print(f'  extension -> {dst_extension}')
    print(f'  backend/local_backend.py -> {dst_backend}')
    print(f'  v2-pipeline/estimate_v2b.py -> {dst_v2}')


if __name__ == '__main__':
    main()
