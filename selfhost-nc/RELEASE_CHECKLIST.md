# Release Checklist (Selfhost NC)

- [ ] `cd selfhost-nc && bash scripts/install.sh`
- [ ] `bash scripts/start_backend.sh`
- [ ] `curl http://127.0.0.1:8787/health`
- [ ] Load unpacked extension from `selfhost-nc/extension`
- [ ] Verify sidepanel settings show Analysis Mode toggle
- [ ] Verify no-CUDA flow prompts once, persists choice
- [ ] Verify single-photo analysis still works
- [ ] `cd selfhost-nc && bash scripts/smoke_playwright_no_cuda.sh`
- [ ] Verify multi-photo DUSt3R on CUDA machine
- [ ] Re-check `THIRD_PARTY_NOTICES.md` before publishing
