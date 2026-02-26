# SleepEasy Repo Handoff

## Canonical Runtime

Use `selfhost/` only for install, testing, and distribution.

- Backend: `selfhost/backend/local_backend.py`
- Extension: `selfhost/extension/`
- Pipeline: `selfhost/v2-pipeline/estimate_v2b.py`
- Install/start scripts: `selfhost/scripts/`

## Research and Legacy

The following are preserved for prior experiments and historical reference:

- `sqft-from-photos/` (experimental pipelines, datasets, run artifacts)
- `research/backend-archived/` (legacy backend)
- `docs/legacy/HANDOFF_legacy.md` (historical implementation notes)

## Maintainer Rule

If you are shipping or validating the deliverable, do not use paths outside `selfhost/`.
