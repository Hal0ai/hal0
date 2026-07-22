# Handoff — brain + embed slot/agent cleanup on halo150

> **You are going to fix the "errored: brain, embed" doctor warnings on
> halo150 after the latest main reinstall.** The fix is straightforward:
> brain is a container slot (not an agent), embed is not needed.
>
> Issue: (none filed yet — write one after this is done, linking the PR)
>
> Read in this order: (1) this handoff, (2) the code sections referenced
> below, (3) `src/hal0/slots/manager.py` (slot-to-unit rendering), (4)
> `src/hal0/cli/agent_shim.py` (hal0-agent dispatch).

---

## 1. What's wrong

A clean reinstall on halo150 (10.0.1.150, privileged LXC, podman 4.9.3) from
latest `main` produces:

```
🛑 Runners: 9/11 healthy — errored: brain, embed
```

**Both brain and embed are being spawned as container slots AND as hal0 agents,
and neither works:**

| unit | loaded | active | why |
| ------ | -------- | -------- | ----- |
| `hal0-slot@brain.service` | loaded | **failed** | model `tmp-model-8d60ae4b` registration incomplete (missing `model_file` row — fixed separately) |
| `hal0-agent@brain.service` | loaded | **failed** | `hal0-agent: unknown agent id 'brain' — drop /etc/hal0/agents/brain.toml with a 'type = ...' field` |
| `hal0-slot@embed.service` | loaded | **failed** | same model registration issue |
| `hal0-agent@embed.service` | absent (graceful) | — | embed is `enabled=false` so the agent wasn't spawned, but the slot unit still fails |

The **root cause** is that the slot orchestrator spawns BOTH a container unit
AND an agent unit for these slots, but:

- brain/embed are not valid `hal0-agent` types (only `hermes` is)
- brain should only run as a container slot (like on lxc105)
- embed is not needed at all

For comparison, lxc105 (the working reference):

```
hal0-slot@brain.service  →  active (running)  ← container slot, NOT an agent
hal0-agent@hermes.service  →  active (running)  ← the only agent
```

lxc105's brain.toml is a standard container slot with `runtime = "container"`.

---

## 2. Why both units spawn

The slot orchestrator (`src/hal0/slots/manager.py`) renders unit files from
`/etc/hal0/slots/*.toml`. For each slot it generates:

1. `hal0-slot@<name>.service` — the **container** runner (podman invocation)
2. `hal0-agent@<name>.service` — the **hal0-agent** CLI shim, if the slot name
   maps to a recognized agent identity

The agent mapping currently matches on slot *name* unconditionally for certain
name patterns (possibly all llm types). The `hal0-agent` CLI (`src/hal0/cli/agent_shim.py`)
then validates the name against `hal0 agent list` and rejects unknown IDs.

The fix needs to happen at the **slot → unit rendering layer**, not the agent
validation layer.

---

## 3. Fix plan

### 3a. Halt agent unit generation for brain

**File:** `src/hal0/slots/manager.py`

The function that generates `hal0-agent@<name>.service` units must skip brain
(and any other slot that isn't a true hal0 agent). Two options:

**Option A (explicit blocklist)** — Add brain to an explicit exclude list:

```python
# Slots that are NOT hal0 agents (container-only inference slots)
_NON_AGENT_SLOTS = {"brain", "embed", "coder"}
```

Then check `if slot_name in _NON_AGENT_SLOTS: return` before the agent unit
generation call.

**Option B (opt-in)** — Only generate agent units for slots that have a matching
`/etc/hal0/agents/<name>.toml` file with a valid `type` field. This is more
future-proof (any new agent type Just Works if it has a config file).

Pick whichever matches the existing code patterns better — search for
"agent" in manager.py to find the right insertion point.

### 3b. Fix brain slot config

**File:** `/etc/hal0/slots/brain.toml` (on disk, not in repo)

The current brain.toml on halo150 is missing `runtime = "container"` and uses
a stale model ID. Fix:

```toml
name = "brain"
type = "llm"
runtime = "container"
device = "gpu-vulkan"
profile = "vkfpx-dense-minicpm5"   # or "moe" — match the actual profile on disk
port = 8089
enable_thinking = true
parallel = 2
chat_template = "auto"

[model]
default = "hal0-brain-sft-fpx8"    # the actual registered model, not tmp-model
context_size = 32000
```

If the model `hal0-brain-sft-fpx8` doesn't exist on 150, register it with
`hal0 model add /mnt/ai-models/chat/hal0-brain-sft-fpx8-agent/model.gguf --id hal0-brain-sft-fpx8`
(or whatever the actual path is — check `/mnt/ai-models/chat/hal0-brain-sft*`).

### 3c. Disable embed slot

```bash
# Set enabled=false and stop the unit
sed -i 's/enabled = true/enabled = false/' /etc/hal0/slots/embed.toml
systemctl disable --now hal0-slot@embed.service
```

Or just delete `/etc/hal0/slots/embed.toml` entirely if embed is never needed.

### 3d. Backfill model_file registration (if needed)

The `hal0 migrate model-layout` tool needs a `model_file` row for every
registered model. On 150, `tmp-model-8d60ae4b` had its `model_file` row
inserted manually (the insert command is in the reinstall session log). If
you switch to `hal0-brain-sft-fpx8`, that model may also need registration.

---

## 4. Slot → unit generation code

### Key files

| File | What it does |
| ------ | ------------- |
| `src/hal0/slots/manager.py` | Reads slot TOMLs, renders systemd units for container + agent |
| `src/hal0/cli/agent_shim.py` | `hal0-agent` CLI — validates agent ID, dispatches to agent driver |
| `src/hal0/agents/hermes_provision.py` | Provisions the hermes agent (the only agent type currently) |

### What to modify

The agent unit generation code path. Start by searching `manager.py` for
`hal0-agent` or `agent` to find where the unit file is created, then add
the skip condition.

If the agent unit generation is entirely template-driven (the installer's
`hal0-agent@.service` template + a `systemctl enable` call), the fix is at the
systemctl enable point — skip `systemctl enable hal0-agent@brain` in the
post-install slot-enable loop.

Look at the installer's step that enables slots: `installer/install.sh`
around line where `hal0 setup --first-run` seeds slots and enables units.

---

## 5. Acceptance

- [ ] `hal0-agent@brain.service` is **not** spawned after install
- [ ] `hal0-slot@brain.service` starts successfully (container runs, model loads)
- [ ] `hal0-slot@embed.service` is disabled or absent
- [ ] `hal0 doctor all` shows **11/11 healthy** (no brain/embed errors)
- [ ] The fix works on a **clean reinstall** (not just an in-place fix)

---

## 6. Quick reference

| Resource | Path / command |
| ---------- | --------------- |
| Reference brain config (lxc105) | `ssh root@10.0.1.142 cat /etc/hal0/slots/brain.toml` |
| Current brain config (150) | `ssh root@10.0.1.150 cat /etc/hal0/slots/brain.toml` |
| Slot orchestrator | `src/hal0/slots/manager.py` |
| Agent CLI shim | `src/hal0/cli/agent_shim.py` |
| Installer slot-setup step | `installer/install.sh` (search `hal0 setup`) |
| <hal0-agent@.service> template | `installer/install.sh` (search `hal0-agent@.service`) |
| Check agent render | `ssh root@10.0.1.150 systemctl list-units 'hal0-agent@*'` |

---

## 7. After this lands

- Re-run the clean install on 150
- Verify `hal0 doctor all` → 11/11 healthy (no brain/embed errors)
- If brain slot still fails on model, fix `/etc/hal0/slots/brain.toml` model_id to point at `hal0-brain-sft-fpx8`
- Delete this handoff once the fix is verified on a clean reinstall
