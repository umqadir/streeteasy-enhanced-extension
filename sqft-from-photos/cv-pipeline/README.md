# cv-pipeline (sqft from photos)

Implements the pipeline described in `docs/PROJECT-PLAN.md`:

- Multi-view SfM (COLMAP) when possible
- Metric scale via zero-shot metric depth models
- Floor plane + footprint extraction → sqft estimate
- Diagnostics + uncertainty interval (v0 heuristic, calibrate later)

Project status + “what to run next” is tracked in `../STATUS.md`.

## Quick start (RunPod)

See `docs/RUNPOD.md`.

## Local dev

This repo uses `uv` for Python management.

```bash
cd cv-pipeline
uv sync
uv run cv-pipeline --help
```

Notes:

- `cv-pipeline run` defaults to a **depth-only fallback** unless you pass `--colmap`.
- `cv-pipeline eval-streeteasy` runs the **COLMAP + metric-depth** path by default.
