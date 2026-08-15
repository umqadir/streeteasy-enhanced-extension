# Project status: finalized

SleepEasy (StreetEasy enhanced extension) was finalized and published on 2026-06-12 and is a completed portfolio project, not active work. It shipped as a free, self-hosted, non-commercial tool using the DUSt3R multiview pipeline (5.4% benchmark error). The MIT-only commercial path (~20% error, multiview blocked on pose estimation) is documented as a concluded research finding in [RESEARCH.md](RESEARCH.md).

Published artifacts: public repo (umqadir/streeteasy-enhanced-extension) with portfolio README and screenshots, a GitHub Pages landing site (https://umqadir.github.io/streeteasy-enhanced-extension/, including /privacy.html), and release v1.1.0 with two zips — `sleepeasy-extension.zip` (load-unpacked / nested) and `sleepeasy-chrome-store-v1.1.0.zip` (flat, manifest at root, for store upload).

## Chrome Web Store submission

A full submission kit is staged in [chrome-web-store/](chrome-web-store/): `LISTING.md` has all paste-ready fields, `SUBMIT.md` is the checklist, and `screenshots/` holds three 1280x800 images. Submission is blocked on three human-only gates: Google re-auth for uzairq93@gmail.com, the one-time $5 developer registration, and the final Publish click. To finish, follow `chrome-web-store/SUBMIT.md` and upload the store zip.

## Local disk cleanup

Two large local-only, gitignored leftovers are safe to delete if disk space is needed:

- ~6 GB of research runs under `sqft-from-photos/v2-pipeline/runs/`
- ~3.4 GB of models in `~/.cache/cv_pipeline`
