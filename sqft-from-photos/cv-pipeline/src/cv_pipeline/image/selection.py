from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from cv_pipeline.image.preprocess import list_images


@dataclass(frozen=True)
class ImageSelectionSpec:
    """
    Selection is applied in two stages:
      1) If any include criteria exist, start from the union of includes (else start from all images).
      2) Remove any excludes.

    Index-based selection always refers to the deterministic ordering of `list_images(images_dir)`
    (sorted recursive file list).
    """

    include_globs: list[str] = field(default_factory=list)
    exclude_globs: list[str] = field(default_factory=list)
    include_indices: list[str] = field(default_factory=list)  # e.g. ["0-10,12"]
    exclude_indices: list[str] = field(default_factory=list)
    include_names: list[str] = field(default_factory=list)  # basenames or relative paths
    exclude_names: list[str] = field(default_factory=list)

    def has_includes(self) -> bool:
        return bool(self.include_globs or self.include_indices or self.include_names)


def _parse_index_expr(expr: str, *, n: int) -> tuple[set[int], list[str]]:
    """
    Parse an index expression like:
      - "0,1,5-8"
      - "3-"  (3..n-1)
      - "-4"  (0..4)
    """
    expr = str(expr or "").strip()
    if not expr:
        return set(), []

    bad: list[str] = []
    out: set[int] = set()
    parts = [p for p in re.split(r"[,\s]+", expr) if p]
    for part in parts:
        if "-" not in part:
            try:
                idx = int(part)
            except Exception:
                bad.append(part)
                continue
            if 0 <= idx < n:
                out.add(idx)
            else:
                bad.append(part)
            continue

        if part.count("-") != 1:
            bad.append(part)
            continue

        a_s, b_s = part.split("-", 1)
        a_s = a_s.strip()
        b_s = b_s.strip()
        if a_s == "" and b_s == "":
            bad.append(part)
            continue

        if a_s == "":
            start = 0
        else:
            try:
                start = int(a_s)
            except Exception:
                bad.append(part)
                continue

        if b_s == "":
            end = n - 1
        else:
            try:
                end = int(b_s)
            except Exception:
                bad.append(part)
                continue

        if start < 0 or end < 0 or start > end:
            bad.append(part)
            continue

        for idx in range(start, end + 1):
            if 0 <= idx < n:
                out.add(idx)
    return out, bad


def _read_list_file(path: Path) -> list[str]:
    items: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        items.append(line)
    return items


def parse_filter_file(path: Path) -> ImageSelectionSpec:
    """
    Simple text format, one directive per line:

      include_index: 0-10,12
      exclude_index: 7,8
      include_glob: **/*.jpg
      exclude_glob: **/*exterior*
      include_name: photo_00.jpg
      exclude_name: photo_19.jpg
      include_file: include.txt   # newline-separated names/paths
      exclude_file: exclude.txt

    Lines without ":" are treated as `exclude_name:` for convenience.
    """
    include_globs: list[str] = []
    exclude_globs: list[str] = []
    include_indices: list[str] = []
    exclude_indices: list[str] = []
    include_names: list[str] = []
    exclude_names: list[str] = []

    base = path.parent
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if ":" not in line:
            exclude_names.append(line)
            continue

        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if not value:
            continue

        if key in {"include_index", "include_indices"}:
            include_indices.append(value)
        elif key in {"exclude_index", "exclude_indices"}:
            exclude_indices.append(value)
        elif key in {"include_glob", "include"}:
            include_globs.append(value)
        elif key in {"exclude_glob", "exclude"}:
            exclude_globs.append(value)
        elif key in {"include_name", "include_path", "include_paths"}:
            include_names.append(value)
        elif key in {"exclude_name", "exclude_path", "exclude_paths"}:
            exclude_names.append(value)
        elif key in {"include_file"}:
            p = Path(value)
            if not p.is_absolute():
                p = base / p
            include_names.extend(_read_list_file(p))
        elif key in {"exclude_file"}:
            p = Path(value)
            if not p.is_absolute():
                p = base / p
            exclude_names.extend(_read_list_file(p))
        else:
            # Unknown key: treat as comment-like.
            continue

    return ImageSelectionSpec(
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        include_indices=include_indices,
        exclude_indices=exclude_indices,
        include_names=include_names,
        exclude_names=exclude_names,
    )


def _match_name(images_dir: Path, all_images: list[Path], name_or_relpath: str) -> set[Path]:
    s = str(name_or_relpath).strip().strip('"').strip("'")
    if not s:
        return set()

    # Relative path match
    if "/" in s or "\\" in s:
        rel = s.replace("\\", "/").lstrip("./")
        target = images_dir / rel
        return {target} if target in set(all_images) else set()

    # Basename match (may match multiple files)
    return {p for p in all_images if p.name == s}


def select_image_paths(
    images_dir: Path,
    *,
    spec: ImageSelectionSpec | None = None,
) -> tuple[list[Path], dict[str, object]]:
    all_images = list_images(images_dir)
    diag: dict[str, object] = {
        "images_dir": str(images_dir),
        "n_all": len(all_images),
        "filters": None,
    }

    if spec is None:
        return all_images, diag

    diag["filters"] = {
        "include_globs": list(spec.include_globs),
        "exclude_globs": list(spec.exclude_globs),
        "include_indices": list(spec.include_indices),
        "exclude_indices": list(spec.exclude_indices),
        "include_names": list(spec.include_names),
        "exclude_names": list(spec.exclude_names),
    }

    if not all_images:
        raise ValueError(f"No images found under: {images_dir}")

    rels = [str(p.relative_to(images_dir).as_posix()) for p in all_images]

    # Build include set
    included: set[Path]
    include_bad_tokens: list[str] = []
    include_missing: dict[str, int] = {"glob": 0, "name": 0}
    include_oob: list[str] = []

    if spec.has_includes():
        included = set()
        for pattern in spec.include_globs:
            matched = False
            for p, rel in zip(all_images, rels, strict=True):
                if Path(rel).match(pattern):
                    included.add(p)
                    matched = True
            if not matched:
                include_missing["glob"] += 1

        for name in spec.include_names:
            m = _match_name(images_dir, all_images, name)
            if not m:
                include_missing["name"] += 1
            included |= m

        for expr in spec.include_indices:
            idxs, bad = _parse_index_expr(expr, n=len(all_images))
            include_bad_tokens.extend(bad)
            # Track out-of-range in a coarse way (bad contains oob tokens too)
            include_oob.extend([b for b in bad if re.match(r"^[0-9]+(-[0-9]*)?$", b)])
            for i in idxs:
                included.add(all_images[i])
    else:
        included = set(all_images)

    if spec.has_includes() and not included:
        raise ValueError(
            f"No images matched include filters under: {images_dir} "
            f"(include_globs={spec.include_globs}, include_indices={spec.include_indices}, include_names={spec.include_names})"
        )

    # Build exclude set
    excluded: set[Path] = set()
    exclude_bad_tokens: list[str] = []
    exclude_missing: dict[str, int] = {"glob": 0, "name": 0}

    for pattern in spec.exclude_globs:
        matched = False
        for p, rel in zip(all_images, rels, strict=True):
            if Path(rel).match(pattern):
                excluded.add(p)
                matched = True
        if not matched:
            exclude_missing["glob"] += 1

    for name in spec.exclude_names:
        m = _match_name(images_dir, all_images, name)
        if not m:
            exclude_missing["name"] += 1
        excluded |= m

    for expr in spec.exclude_indices:
        idxs, bad = _parse_index_expr(expr, n=len(all_images))
        exclude_bad_tokens.extend(bad)
        for i in idxs:
            excluded.add(all_images[i])

    selected = [p for p in all_images if p in included and p not in excluded]
    if not selected:
        raise ValueError(f"No images selected under: {images_dir} (after applying filters)")

    diag["selection"] = {
        "n_selected": len(selected),
        "n_excluded": int(len(included) - len(selected)),
        "include_missing": include_missing,
        "exclude_missing": exclude_missing,
        "include_bad_tokens": include_bad_tokens,
        "exclude_bad_tokens": exclude_bad_tokens,
    }
    return selected, diag

