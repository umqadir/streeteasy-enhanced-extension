# Handoff Notes (Selfhost NC)

This folder is the canonical self-host bundle.

## New Behavior

- Multi-photo defaults to DUSt3R (`analysisMode=auto`)
- If CUDA is unavailable, analyze returns `NO_CUDA_MULTI_UNAVAILABLE`
- UI prompts once to switch to single-image mode and persists preference
- Settings include manual analysis mode toggle

## Key Persistence Fields

- `analysisMode`: `auto | single-image`
- `noCudaPromptHandled`: boolean

Stored under extension key: `area:backend:config`

## Backend API Additions

- `/health`
  - `capabilities.cudaAvailable`
  - `capabilities.mpsAvailable`
  - `analysisMode`
  - `recommendation.analysisMode`
- `/backend/config`
  - accepts `analysisMode`
- `/estimate/multi`
  - accepts `multiviewMethod`
