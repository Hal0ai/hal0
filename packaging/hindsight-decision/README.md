# hal0-hs-decide packaging (Hindsight 1.2 showcase)

This directory ships the verified-working CPU "System One" server for
`hindsight-ai/hs-decide-1-flash` (built and measured on CT143 — see
`/var/lib/hal0/work/notes/memory-showcase/PLAN.md`).

- `hs_decide_serve.py` — the server itself. No vLLM: a plain `transformers`
  forward pass on CPU, reusing the official `hindsight_decision` serving
  package shipped in the model repo's `serving/` dir (schema / prompt
  rendering / typed answer shaping stay byte-identical to upstream).
- The matching unit lives at `installer/systemd/hal0-hs-decide.service`.

## Build (not yet wired into install.sh)

```
python3 -m venv /var/lib/hal0/memory/hindsight-decision/.venv
. /var/lib/hal0/memory/hindsight-decision/.venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install "transformers>=5.16.1" fastapi uvicorn accelerate
pip install <hs-decide-1-flash snapshot dir>/serving
```

Measured on shared CPU (CT143): ~0.36s @ 208 tok, 2.3s @ 1.1k tok,
6.8s @ 3.4k tok, 25s @ 8.5k tok.

## Open work

Installer wiring (venv build step, model pre-pull into the memory hf-cache,
gating via `[memory.decision]`, and an idempotent venv-swap-via-symlink
pattern per PLAN.md's constraint) is not yet implemented — see the
`open_questions` in the platform-branch handoff. This directory is the
verified payload the installer step will drive.
