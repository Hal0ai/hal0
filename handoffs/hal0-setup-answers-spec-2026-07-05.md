# `hal0-setup.yaml` — headless answer-file spec (2026-07-05)

Companion to `handoffs/installer-setup-plan-2026-07-05.md`. Specs the
"Headless contract" suggestion (plan line 68): a single answer file that is the
**non-interactive twin of the Stage-2 guided flow**, so `hal0 setup --auto` is
fully reproducible/CI-able and a Proxmox/community-scripts installer can run
Stage 2 unattended instead of dead-ending at an interactive prompt.

Grounded in the real apply model as of this date:
`hal0.install.orchestrate.Selections` / `SlotSelection`, `build_auto_selections`
(`cli/setup_command.py`), `apply_setup`, and `POST /api/install/apply-selections`
(`api/routes/installer.py`).

---

## 1. Why more than `Selections`

The apply core (`Selections`) models only: `storage_dir`, `slots[]`,
`extensions{}`, `npu_opt_in`, `comfyui_defaults`. But the *guided flow*
(plan lines 33–42) also decides network bind, HF token persistence, model-store
co-location, ComfyUI download, and app now/later + gateway — which today live in
env vars / config files, not `Selections`. So the answer file is a **superset**:
each key maps either to a `Selections` field (wired now) or to an env/config
concern owned by a redesign workstream (§5 table says which).

The loader resolves the wired subset into a `Selections` today; unwired keys are
accepted-and-warned (forward-compatible) until their workstream lands.

## 2. CLI surface

```
hal0 setup --answers PATH            # answer-driven interactive-equivalent run
hal0 setup --auto --answers PATH     # fully non-interactive (installer/CI path)
hal0 setup --answers PATH --no-pull  # Stage-1 style: seed configs, no downloads
hal0 setup --plan  [--answers PATH]  # print the "will create" table, write nothing
hal0 setup --emit-answers PATH       # snapshot an interactive run's choices to a file
hal0 setup --answers PATH --resume   # continue from the sentinel step ledger
```

**Precedence** (highest first): explicit CLI flags → answer file → detected /
recommended defaults. Secrets from the environment (`HF_TOKEN` /
`HUGGING_FACE_HUB_TOKEN`) always win over an inlined token. `--emit-answers`
closes the round trip: run it interactively once, commit the emitted file, replay
it forever with `--auto --answers`.

**Sentinel-as-ledger** (`--resume`): the first-run sentinel becomes a per-step
record so a mid-run failure resumes at the last completed step rather than
re-running the whole flow (plan "Resumable setup" suggestion).

## 3. Schema (v1)

`version` is mandatory and gates the loader. Every value accepts the literal
`auto` where a hardware/registry-derived default exists — `auto` runs the *same*
resolver the interactive/`--auto` path already uses (`suggest_models`,
`derive_device`, `derive_profile`, hw budget clamp), so an all-`auto` file is
byte-equivalent to today's `build_auto_selections`.

```yaml
version: 1

# Q1 — network shape (WS-C)
network:
  bind_host: 0.0.0.0        # one value read by BOTH the unit and `hal0 serve` (HAL0_BIND_HOST)
  hostname: hal0            # seeds HAL0_HOSTNAME + mDNS
  public_url: null          # optional reverse-proxy URL; seeds HAL0_ALLOWED_ORIGINS

# Q2/Q4 — model store  (path → Selections.storage_dir; flm_store co-located: WS-D)
model_store:
  path: /var/lib/hal0/models   # absolute; validated writable + free-space
  # flm_store is co-located automatically (same mount); do not set separately

# Q3/Q5 — Hugging Face token (WS-D: persist to root:root secrets/, thread into pulls)
huggingface:
  token_env: HF_TOKEN       # RECOMMENDED — read from this env var at apply time
  token_file: null          # OR a 0600 file path to read (→ secrets/ EnvironmentFile)
  token: null               # discouraged: never inline a token in a committed file

# Q4/Q7 — LLM slots  → Selections.slots[]
slots:
  - capability: chat        # "chat" | "coder"
    name: chat
    port: 8081
    model_id: auto          # auto → suggest_models(capability, hw)
    context_size: auto      # auto → clamp to hw budget (WS-E, fixes the `oom` artifact)
    device: auto            # auto → derive_device(capability, hw, npu_opt_in)
    profile: auto           # auto → derive_profile(device)
    enabled_on_pull: true   # seed disabled, flip enabled on pull success (WS-E)
  - capability: coder
    name: coder
    port: 8082
    model_id: auto          # auto → suggest_models("coder", hw, prefer_coder=True)

# Q5/Q15 — NPU  → Selections.npu_opt_in
npu:
  opt_in: auto              # auto → hw.npu.present AND passthrough healthy

# Q6/Q8 — ComfyUI / image+video gen
gen:
  mode: scaffold_only       # off | scaffold_only | scaffold_and_download (download = WS-G)
  capabilities:             # → Selections.comfyui_defaults [(cap_id, family)]
    txt2img: auto           # cap_id: family — auto = Capability.default_family
    img2img: auto           # families come from comfyui/capabilities.py alternatives
    txt2video: off          # off = omit this capability
    img2video: off
    image_upscale: auto

# Q7/Q9 — apps  → Selections.extensions {id: bool}  (+ when/gateway = WS-H)
apps:
  openwebui: { enabled: true,  when: now }              # id "openwebui"
  hermes:    { enabled: true,  when: now, gateway: off } # gateway: off|telegram|discord
  pi:        { enabled: false }                          # coder agent (default off)
  # NOTE: gen.mode drives the "comfyui" extension enable; don't list it here.
```

## 4. Loader contract

```python
def load_answers(path: str, hw: HardwareInfo) -> Selections
```
- Parse + validate against `version: 1`; reject unknown top-level keys unless
  `strict: false` is set (default false → warn-and-ignore, forward-compatible).
- Resolve every `auto` via the existing resolvers so there is ONE derivation
  path shared with `build_auto_selections` (no divergence between `--auto` and
  `--answers`).
- Map `gen.mode: off` → `extensions["comfyui"] = False`; `scaffold_only` /
  `scaffold_and_download` → `True` + populate `comfyui_defaults` from
  `gen.capabilities`.
- Non-`Selections` keys (network, huggingface, `gen.mode: scaffold_and_download`,
  `apps.*.when`, `apps.hermes.gateway`) are handed to their owning workstream's
  writer as it lands; until then the loader records them and warns "not yet
  applied (WS-X)".
- Return a `Selections`; the rest of the pipeline (`apply_setup` /
  `apply-selections`) is unchanged.

## 5. Field → destination map (what's wired **today** vs blocked)

| YAML path | Lands in | Wired now? | Owner |
|---|---|---|---|
| `network.bind_host` | `HAL0_BIND_HOST` (unit + serve) | partial (thinmint leak live) | WS-C |
| `network.hostname` / `public_url` | `HAL0_HOSTNAME`, `HAL0_ALLOWED_ORIGINS` | no | WS-C |
| `model_store.path` | `Selections.storage_dir` | **yes** | WS-D adds flm_store co-locate + free-space |
| `huggingface.token*` | `apply_setup(hf_token=)` | **env-only today**; secrets/ persist pending | WS-D |
| `slots[].capability/name/port/model_id` | `Selections.slots` / `SlotSelection` | **yes** | — |
| `slots[].model_id: auto` | `suggest_models()` | **yes** | — |
| `slots[].device` / `profile` | `SlotSelection.device/profile` | **yes** | — |
| `slots[].context_size` | context clamp | no | WS-E (Q7) |
| `slots[].enabled_on_pull` | enable-on-pull-success | no | WS-E (Q7) |
| `npu.opt_in` | `Selections.npu_opt_in` | **yes** | — |
| `gen.capabilities` | `Selections.comfyui_defaults` | **yes** (recorded, not pulled) | — |
| `gen.mode: scaffold_and_download` | `POST /api/comfyui/models/fetch` | no (fetch feature-dead) | WS-G (Q8) |
| `apps.{openwebui,hermes,pi}.enabled` | `Selections.extensions` | **yes** | — |
| `apps.*.when` (now/later) | deferred install verbs | no | WS-H (Q9) |
| `apps.hermes.gateway` | gateway enable | no | WS-H (Q9) |

**Takeaway:** the *core* (store path, slots, extensions, npu, comfyui picks) is
implementable now as a thin `--answers` loader over the existing `Selections` —
this can land in Wave 1/2 ahead of the network/token/gen/apps polish, and each
later workstream just wires its own keys.

## 6. Two worked examples

**A — Strix Halo GPU box, curated pulls, OWUI + Hermes:**
```yaml
version: 1
network: { bind_host: 0.0.0.0, hostname: hal0 }
model_store: { path: /mnt/hal0-models }
huggingface: { token_env: HF_TOKEN }
slots:
  - { capability: chat,  name: chat,  port: 8081, model_id: auto }
  - { capability: coder, name: coder, port: 8082, model_id: auto }
npu: { opt_in: auto }
gen: { mode: scaffold_only, capabilities: { txt2img: auto } }
apps:
  openwebui: { enabled: true, when: now }
  hermes:    { enabled: true, when: now, gateway: off }
  pi:        { enabled: false }
```

**B — headless CPU box, minimal, no downloads at provision (`--no-pull`):**
```yaml
version: 1
network: { bind_host: 127.0.0.1 }
model_store: { path: /var/lib/hal0/models }
slots:
  - { capability: chat, name: chat, port: 8081, model_id: auto }
npu: { opt_in: false }
gen: { mode: off }
apps: { openwebui: { enabled: true }, hermes: { enabled: false }, pi: { enabled: false } }
```

## 7. How the Proxmox / community-scripts installer uses it

Stage 1 (this repo's `scripts/proxmox-ve/hal0.sh` or a catalog `install/*.sh`)
drops the file and seeds without downloads; Stage 2 pulls when the operator (or
`--auto`) is ready:

```bash
pct push <CTID> hal0-setup.yaml /etc/hal0/hal0-setup.yaml
pct exec <CTID> -- env HF_TOKEN="$HF_TOKEN" \
    hal0 setup --auto --answers /etc/hal0/hal0-setup.yaml --no-pull   # Stage 1
pct exec <CTID> -- env HF_TOKEN="$HF_TOKEN" \
    hal0 setup --auto --answers /etc/hal0/hal0-setup.yaml             # Stage 2 (pulls)
```

This is the exact thing that unblocks a **community-scripts.org catalog entry**:
their install pipeline is non-interactive and must finish at a working URL — the
answer file lets Stage 2 run to completion without a TTY.

## 8. Security & validation

- **Never commit a token.** `huggingface.token` is discouraged; prefer
  `token_env` (installer passes `HF_TOKEN`) or `token_file` (0600, root:root).
  On apply the token persists to the root-only `secrets/` EnvironmentFile
  (Q5), not `0644 api.env`.
- Validate: `version == 1`; `model_store.path` absolute + writable + free-space;
  slot `port`s unique and free; `capability ∈ {chat, coder}`;
  `gen.capabilities` keys ∈ comfyui `CAPABILITIES`; `apps.*` ids ∈ EXTENSIONS.
- `--plan` runs all validation and prints the "will create" table (slots, ports,
  pulls, bind, store, disk) with zero writes — the safe preview and the CI
  assertion surface.

## 9. Decisions for the user

1. **File location / name** — `/etc/hal0/hal0-setup.yaml` (config dir, survives
   updates) vs a run-local path. Recommend `/etc/hal0/`.
2. **Ship the core loader in Wave 1/2** (store+slots+extensions+npu+comfyui) and
   let network/token/gen-download/apps-when accrete per workstream — vs. hold
   the whole answer file until Stage-2 (WS-F) is built. Recommend ship-core-early;
   it de-risks WS-F and immediately unblocks the Proxmox `--auto` path.
3. **`--emit-answers` now or later** — cheap and high-value for docs/tests;
   recommend include in the first cut.
```
