#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from urllib.request import urlopen


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def git_clone_or_update(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        run(['git', '-C', str(dest), 'fetch', '--all', '--tags'])
        try:
            run(['git', '-C', str(dest), 'pull', '--ff-only'])
        except Exception:
            pass
        return
    run(['git', 'clone', '--depth', '1', url, str(dest)])


def download(url: str, out_path: Path, force: bool) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not force:
        print(f'skip (exists): {out_path}')
        return
    print(f'download: {url} -> {out_path}')
    tmp = out_path.with_suffix(out_path.suffix + '.tmp')
    if tmp.exists():
        tmp.unlink()
    with urlopen(url, timeout=120) as resp, tmp.open('wb') as f:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(out_path)


def bootstrap_moge(repo_id: str, out_dir: Path, force: bool) -> None:
    from huggingface_hub import snapshot_download

    print(f'snapshot_download: {repo_id} -> {out_dir}')
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(out_dir),
        local_dir_use_symlinks=False,
        resume_download=True,
        force_download=force,
        max_workers=8,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Download DUSt3R + MoGe assets for selfhost release')
    p.add_argument('--cache-root', type=Path, default=Path.home() / '.cache' / 'cv_pipeline' / 'models')
    p.add_argument('--force', action='store_true')
    p.add_argument('--moge-repo', default='Ruicheng/moge-2-vitl-normal')
    p.add_argument('--skip-vendor', action='store_true')
    return p.parse_args()


def main() -> None:
    args = parse_args()
    models_root = args.cache_root
    vendor_dir = models_root / 'vendor'
    checkpoints_dir = models_root / 'checkpoints'

    if not args.skip_vendor:
        git_clone_or_update('https://github.com/naver/dust3r.git', vendor_dir / 'dust3r')
        git_clone_or_update('https://github.com/microsoft/MoGe.git', vendor_dir / 'moge')

    dust3r_url = 'https://download.europe.naverlabs.com/ComputerVision/DUSt3R/DUSt3R_ViTLarge_BaseDecoder_512_dpt.pth'
    dust3r_ckpt = checkpoints_dir / 'dust3r' / 'DUSt3R_ViTLarge_BaseDecoder_512_dpt.pth'
    download(dust3r_url, dust3r_ckpt, force=args.force)

    moge_out = checkpoints_dir / 'moge' / args.moge_repo.replace('/', '__')
    bootstrap_moge(args.moge_repo, moge_out, force=args.force)

    print('\nDone.')
    print(f'  vendor: {vendor_dir}')
    print(f'  checkpoints: {checkpoints_dir}')


if __name__ == '__main__':
    main()
