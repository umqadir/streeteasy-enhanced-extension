# SleepEasy

This repository contains both:
- a canonical self-hosted distributable bundle
- retained research/experiment work

## Canonical Distribution Folder

Use `selfhost-nc/` for install, run, and release packaging.

Quick start:

```bash
cd selfhost-nc
bash scripts/install.sh
bash scripts/start_backend.sh
```

Then load unpacked extension from `selfhost-nc/extension` in `chrome://extensions`.

Full instructions:
- `selfhost-nc/README.md`

## Repository Layout

- `selfhost-nc/`: canonical non-commercial self-host bundle (extension + backend + v2 pipeline + scripts)
- `sqft-from-photos/`: research/experiments and data collection work
- `backend-archived/`: legacy backend code kept for reference
- `docs/`, `scripts/`: project utilities and reference docs

## Notes

- If you are shipping or testing the distributable, do not use ad-hoc paths from other folders; use `selfhost-nc/` only.
- Local artifacts (`.playwright*`, `.artifacts`, runtime run folders) are intentionally ignored.
