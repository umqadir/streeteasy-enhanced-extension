#!/usr/bin/env python3

import argparse
import json
from pathlib import Path

from PIL import Image


def _has_exif(path: Path) -> bool:
    with Image.open(path) as img:
        exif = img.getexif()
        return bool(exif and len(exif) > 0)


DATA_DIR = Path(__file__).parent.parent / "data"


def main() -> None:
    parser = argparse.ArgumentParser(description="Check EXIF presence in downloaded JPGs.")
    parser.add_argument(
        "--downloads-dir",
        type=Path,
        default=DATA_DIR / "downloads",
    )
    args = parser.parse_args()

    files = sorted([p for p in args.downloads_dir.rglob("*") if p.is_file()])
    checked = 0
    with_exif = 0
    with_exif_paths: list[str] = []
    errors: list[dict[str, str]] = []

    for p in files:
        if p.suffix.lower() not in {".jpg", ".jpeg"}:
            continue
        checked += 1
        try:
            if _has_exif(p):
                with_exif += 1
                if len(with_exif_paths) < 20:
                    with_exif_paths.append(str(p))
        except Exception as e:  # noqa: BLE001
            errors.append({"path": str(p), "error": str(e)})

    print(
        json.dumps(
            {
                "downloadsDir": str(args.downloads_dir),
                "checked": checked,
                "withExif": with_exif,
                "withExifPathsSample": with_exif_paths,
                "errorsSample": errors[:20],
                "errorsCount": len(errors),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

