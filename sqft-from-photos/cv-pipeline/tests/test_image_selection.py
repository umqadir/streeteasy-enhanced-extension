from __future__ import annotations

from pathlib import Path

from cv_pipeline.image.selection import ImageSelectionSpec, parse_filter_file, select_image_paths


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")


def test_select_image_paths_indices(tmp_path: Path) -> None:
    root = tmp_path / "imgs"
    _touch(root / "a.jpg")
    _touch(root / "b.jpg")
    _touch(root / "c.jpg")

    # Stable ordering is alphabetical.
    sel, diag = select_image_paths(root, spec=ImageSelectionSpec(include_indices=["1-2"]))
    assert [p.name for p in sel] == ["b.jpg", "c.jpg"]
    assert diag["selection"]["n_selected"] == 2


def test_parse_filter_file_and_apply(tmp_path: Path) -> None:
    root = tmp_path / "imgs"
    _touch(root / "photo_00.jpg")
    _touch(root / "photo_01.jpg")
    _touch(root / "photo_02.jpg")
    _touch(root / "photo_03.jpg")

    f = tmp_path / "filter.txt"
    f.write_text(
        "\n".join(
            [
                "include_index: 0-3",
                "exclude_index: 1",
                "exclude_name: photo_03.jpg",
                "",
            ]
        ),
        encoding="utf-8",
    )

    spec = parse_filter_file(f)
    sel, _ = select_image_paths(root, spec=spec)
    assert [p.name for p in sel] == ["photo_00.jpg", "photo_02.jpg"]

