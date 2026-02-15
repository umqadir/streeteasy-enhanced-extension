# Codex Run Notes

Use these commands for the local curation UI (`sample-collection/scripts/curate_web.py`).

- Do not use `launchctl` here.
- Do not rely on one-shot `nohup ... &` from Codex tool calls for long-lived servers; those can be reaped after the tool invocation ends.
- Use a detached `screen` session for persistent local dev serving.

Start:

```bash
cd /Users/uzairqadir/Projects/data-projects/national/crimerisk-clone/streeteasy-enhanced-extension/sqft-from-photos
mkdir -p sample-collection/.run
screen -S curate_web -X quit || true
screen -dmS curate_web bash -lc 'cd /Users/uzairqadir/Projects/data-projects/national/crimerisk-clone/streeteasy-enhanced-extension/sqft-from-photos && python sample-collection/scripts/curate_web.py --dataset sample-collection/streeteasy_eval_dataset/listings.json --host 127.0.0.1 --port 7860 >> sample-collection/.run/curate_web.log 2>&1'
```

Verify:

```bash
screen -ls | rg curate_web
lsof -nP -iTCP:7860 -sTCP:LISTEN
curl -sS http://127.0.0.1:7860/api/meta
```

Stop:

```bash
screen -S curate_web -X quit || true
```
