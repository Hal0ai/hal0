# haloai → hal0 migration

This document covers the v1 cutover from the legacy `haloai` install to
`hal0`. **Scope is intentionally narrow:** only models are migrated.
Slots, providers, upstreams, and OpenWebUI state all start fresh — the
operator runs the FirstRun wizard post-install to bind models to slots.

The script that does the work is [`scripts/migrate-haloai.py`](../scripts/migrate-haloai.py).

## What's migrated, what isn't

| Carries over | Starts fresh |
|---|---|
| Curated allow-list of model files (registered in hal0's model registry; the GGUF/safetensors bytes stay where they are on `/mnt/ai-models`) | Slot configs — re-create via FirstRun wizard (hardware-aware) |
| | `providers.toml` — re-enter API keys (OpenRouter, Anthropic, etc.) |
| | `upstreams.toml` — re-add external endpoints |
| | OpenWebUI state — chat history, user accounts, configs |
| | Agents, memories, projects, kanban, training, RAG, fixlog, vault, skills (all stripped from v1, PLAN §1) |

## Curated model allow-list

`DEFAULT_ALLOWLIST` ships with **14 entries**, all chat / code LLMs the
haloai install used as daily drivers. Each entry resolves to the largest
matching file in `/mnt/ai-models/huggingface/hub/models--<org>--<repo>/snapshots/<sha>/`.
Missing entries are skipped with a WARN log (not a hard failure).

| id | hf_repo | role |
|---|---|---|
| `qwen3-coder-next` | `unsloth/Qwen3-Coder-Next-GGUF` | code/chat |
| `qwen3-coder-next-mxfp4` | `amd/Qwen3-Coder-Next-MXFP4` | code/chat/NPU |
| `qwen3.6-27b` | `unsloth/Qwen3.6-27B-GGUF` | chat |
| `qwen3.6-35b-a3b` | `unsloth/Qwen3.6-35B-A3B-GGUF` | chat (MoE) |
| `qwen3.6-27b-heretic-neo-code` | `DavidAU/Qwen3.6-27B-Heretic-...-MAX-GGUF` | code/uncensored |
| `qwen3-coder-reap-25b` | `bartowski/cerebras_Qwen3-Coder-REAP-25B-A3B-GGUF` | code |
| `qwen3-zero-coder-reasoning-0.8b-neo` | `DavidAU/Qwen3-Zero-Coder-Reasoning-V2-0.8B-NEO-EX-GGUF` | code (tiny) |
| `qwen3-next-80b-thinking` | `cpatonn/Qwen3-Next-80B-A3B-Thinking-AWQ-4bit` | chat (reasoning) |
| `qwen3.5-0.8b` | `unsloth/Qwen3.5-0.8B-GGUF` | chat (tiny) |
| `qwen3.5-4b` | `unsloth/Qwen3.5-4B-GGUF` | chat |
| `qwen3.5-9b` | `unsloth/Qwen3.5-9B-GGUF` | chat |
| `qwen3.5-35b-a3b` | `unsloth/Qwen3.5-35B-A3B` | chat (raw weights, MoE) |
| `kappa-20b-mxfp4` | `eousphoros/kappa-20b-131k-mxfp4` | chat (NPU) |
| `kappa-20b-i1-gguf` | `mradermacher/kappa-20b-131k-i1-GGUF` | chat |

To override, pass `--allowlist <file.toml>` with a `[[models]]` array.

### Models that need a post-cutover `hal0 model pull`

These were requested but aren't on the haloai LXC's `/mnt/ai-models` —
the script silently skips them (per design). Pull them after cutover:

- **Llama 4** (any variant)
- **Llama 4 Scout** — `meta-llama/Llama-4-Scout-*-Instruct` (fill exact repo at pull time)
- **Nemotron 115B** — `nvidia/Llama-3.1-Nemotron-*` family

Use `hal0 model pull <hf-repo>` after the migration is in place.

## Cutover sequence (PLAN §11)

Run from the **haloai LXC** as a sudo-capable user. The script is read-only
against `/mnt/ai-models`; only the staging dir gets written.

### Phase 0 — backups (do these even if nothing else)

```bash
sudo tar czf /root/haloai-backup-$(date +%F).tar.gz \
    /opt/haloai/openwebui/webui.db \
    /opt/haloai/config/{providers.toml,upstreams.toml,haloai.toml} \
    /opt/haloai/data/slots/ 2>/dev/null || true
```

Even though v1 starts fresh on the openwebui + providers side, hang on to
the backup until you've confirmed FirstRun + chat works end-to-end.

### Phase 1 — dry-run the migration

```bash
cd /path/to/hal0  # or wherever the hal0 source/install lives
python3 scripts/migrate-haloai.py \
    --hub-root /mnt/ai-models/huggingface/hub \
    --output   /tmp/hal0-migration-out \
    --dry-run
```

Check the summary printed at the end:

- `resolved: N` — how many allow-list entries actually found files on disk.
- `skipped: M` — entries missing from `/mnt/ai-models` (just informational; not an error).

If `resolved` is zero, double-check `--hub-root` matches your actual HF cache layout.

### Phase 2 — install hal0 fresh

```bash
# Stop the old stack first
sudo systemctl stop 'haloai-*.service' 'hermes-*.service' 2>/dev/null || true

# Install hal0
sudo bash installer/install.sh
```

The installer writes `/etc/hal0/`, `/var/lib/hal0/`, and the systemd
template at `hal0-slot@.service`. It does **not** auto-start any slots —
that's the FirstRun wizard's job.

### Phase 3 — write the migrated registry

```bash
python3 scripts/migrate-haloai.py \
    --hub-root /mnt/ai-models/huggingface/hub \
    --output   /tmp/hal0-migration-out

sudo rsync -av /tmp/hal0-migration-out/var/lib/hal0/registry/ \
                /var/lib/hal0/registry/
sudo chown -R hal0:hal0 /var/lib/hal0/registry/
```

### Phase 4 — start hal0 + verify

```bash
sudo systemctl start hal0-api.service hal0-openwebui.service
hal0 model list                 # should show the resolved allow-list
hal0 status                     # API up, no slots loaded yet
```

Open the dashboard at `http://<host>:8080`. The FirstRun wizard will
prompt you to pick a model and assign it to the `primary` slot.

### Phase 5 — confirm end-to-end before tearing the old stack down

1. FirstRun wizard binds a model → primary slot reports `ready`.
2. Visit OpenWebUI at `http://<host>:3001` → confirm chat works.
3. After a week of stability:

   ```bash
   sudo rm -rf /opt/haloai /root/.hermes-next
   ```

Disable + remove the systemd units last so you can roll back if needed:

```bash
sudo systemctl disable 'haloai-*.service' 'hermes-*.service' 2>/dev/null || true
sudo rm -f /etc/systemd/system/haloai-*.service /etc/systemd/system/hermes-*.service
sudo systemctl daemon-reload
```

## Verification

```bash
# Registry parses
hal0 model list

# Each model resolves on disk
hal0 model list --format json | jq -r '.[] | .path' | xargs -I{} test -e {}
echo "all resolved: $?"      # 0 = good

# Round-trip a request
curl -s http://127.0.0.1:8080/v1/models | jq .
```

## Rollback

The script's only mutation is writing files under `--output` (a staging
dir you control). If Phase 3 (the rsync) already happened and you want
to revert before any data is written:

```bash
sudo rm /var/lib/hal0/registry/registry.toml
sudo systemctl restart hal0-api
```

`hal0 model list` will return empty — operator can then re-run the
migration or fall back to the haloai stack (which still has all its
data intact under `/opt/haloai/` until Phase 5).

## Cleaning stale capability selections (`hal0 capabilities migrate`)

The model-first picker reshape (commit b90a569) added per-`(backend,
model)` validation to the capabilities overlay: any selection that mixes
a backend with a model the backend can't serve (e.g. `backend=npu` +
an llama.cpp GGUF, or `backend=vulkan` + an FLM-only chat tag) is now
rejected at apply time with `capability.illegal_backend_model_pair`.

The runtime guard only protects writes. Already-persisted selections
in `/etc/hal0/capabilities.toml` survive until they're touched again.
Run the migration once after upgrading to snap legacy selections to
something the live catalog will accept:

```bash
hal0 capabilities migrate --dry-run   # show what would change
hal0 capabilities migrate             # rewrite + persist atomically
```

Behaviour by selection state (`src/hal0/cli/capabilities_commands.py`):

- **legal** — left alone.
- **illegal `(backend, model)` pair** — backend is snapped to the
  model's first legal backend (and provider re-resolved against the
  matching catalog row). The model selection is preserved.
- **unknown model** — model is cleared; backend stays so the dashboard
  still shows what was previously chosen.

Idempotent — running twice is a no-op once everything is legal. Safe
to run pre-/post-upgrade.

## CLI reference

| Flag | Default | Notes |
|---|---|---|
| `--hub-root` | `/mnt/ai-models/huggingface/hub` | HF cache root. |
| `--output` | `/tmp/hal0-migration-out` | Staging dir for the generated `var/lib/hal0/registry/registry.toml`. |
| `--allowlist` | (built-in 14-entry list) | TOML file with `[[models]]` array to override the curated set. |
| `--dry-run` | off | Resolve + validate, write nothing. |
| `--force` | off | Wipe `--output` if it's non-empty. |

Exit codes: `0` success, `2` `MigrationError` (missing hub, bad
allow-list TOML, refused to clobber).
