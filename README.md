# SleepEasy

This repository contains both:
- a canonical self-hosted distributable bundle
- retained research/experiment work

## Canonical Distribution Folder

Use `selfhost/` for install, run, and release packaging.

Quick start:

```bash
cd selfhost
bash scripts/install.sh
bash scripts/start_backend.sh
```

Then load unpacked extension from `selfhost/extension` in `chrome://extensions`.

Full instructions:
- `selfhost/README.md`

## Repository Layout

- `selfhost/`: canonical non-commercial self-host bundle (extension + backend + v2 pipeline + scripts)
- `sqft-from-photos/`: research/experiments and data collection work
- `research/backend-archived/`: legacy backend code kept for reference
- `docs/`, `scripts/`: project utilities and reference docs

## Notes

- If you are shipping or testing the distributable, do not use ad-hoc paths from other folders; use `selfhost/` only.
- Local artifacts (`.playwright*`, `.artifacts`, runtime run folders) are intentionally ignored.
