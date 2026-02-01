from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


def build_image_manifest(images_dir: Path, image_paths: list[Path]) -> dict[str, object]:
    images = []
    for i, p in enumerate(image_paths):
        try:
            rel = str(p.relative_to(images_dir).as_posix())
        except Exception:
            rel = p.name
        images.append({"index": i, "rel_path": rel, "name": p.name})
    return {"images_dir": str(images_dir), "n_images": len(images), "images": images}


def write_contact_sheet(
    image_paths: list[Path],
    *,
    out_path: Path,
    cols: int = 6,
    thumb_size: int = 256,
    max_images: int = 120,
    pad: int = 10,
    title: str | None = None,
) -> dict[str, object]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = min(int(max_images), len(image_paths))
    if n <= 0:
        raise ValueError("No images to render")

    cols = max(1, int(cols))
    rows = int(math.ceil(n / float(cols)))
    label_h = 20
    cell_w = int(thumb_size)
    cell_h = int(thumb_size) + label_h
    title_h = 40 if title else 0

    w = pad + cols * (cell_w + pad)
    h = pad + title_h + rows * (cell_h + pad)
    canvas = Image.new("RGB", (w, h), (250, 250, 250))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()

    y0 = pad
    if title:
        draw.text((pad, pad), title, fill=(20, 20, 20), font=font)
        y0 += title_h

    for i in range(n):
        src = image_paths[i]
        try:
            img = Image.open(src)
            img = ImageOps.exif_transpose(img).convert("RGB")
        except Exception:
            img = Image.new("RGB", (thumb_size, thumb_size), (200, 200, 200))

        img.thumbnail((thumb_size, thumb_size), Image.Resampling.LANCZOS)
        thumb = Image.new("RGB", (thumb_size, thumb_size), (30, 30, 30))
        ox = (thumb_size - img.size[0]) // 2
        oy = (thumb_size - img.size[1]) // 2
        thumb.paste(img, (ox, oy))

        r = i // cols
        c = i % cols
        x = pad + c * (cell_w + pad)
        y = y0 + r * (cell_h + pad)
        canvas.paste(thumb, (x, y))

        # Draw index label with a small background for readability.
        idx_text = f"#{i:03d}"
        tw, th = draw.textbbox((0, 0), idx_text, font=font)[2:]
        draw.rectangle((x + 2, y + 2, x + 2 + tw + 6, y + 2 + th + 4), fill=(0, 0, 0))
        draw.text((x + 5, y + 4), idx_text, fill=(255, 255, 255), font=font)

        name = src.name
        name = name if len(name) <= 30 else (name[:27] + "…")
        draw.text((x, y + thumb_size + 2), name, fill=(20, 20, 20), font=font)

    canvas.save(out_path, format="JPEG", quality=90, optimize=True)
    return {
        "out_path": str(out_path),
        "n": int(n),
        "cols": int(cols),
        "rows": int(rows),
        "thumb_size": int(thumb_size),
    }
