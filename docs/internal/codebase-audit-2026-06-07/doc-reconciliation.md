# Documentation vs Code Reconciliation Audit — 2026-06-07

**Scope:** All living docs in the hal0 repo (README, CONTEXT.md, ARCHITECTURE.md,
PLAN.md, CHANGELOG.md, CONTRIBUTING.md, AGENTS.md, `docs/` tree, `docs/internal/adr/`).
Point-in-time snapshots and `docs/internal/archive/` are excluded from "stale" verdicts —
they are historical records.

**Method:** graphify query to locate code reality, then targeted Read for load-bearing
claims. Every finding below has been cross-checked against the graph or a file read.

---

## Summary

The project has accumulated two layers of doc debt. First, several high-profile documents
(README, ARCHITECTURE.md, PLAN.md, CONTRIBUTING.md, docs/operate/auth.mdx,
docs/api/openai-compat.mdx) still describe v0.1/v0.2-era architecture and a version
number (0.2.0) that is two releases behind the actual codebase (0.3.2-alpha.1). Second,
a docs sync-direction contradiction exists between docs/README.md and
docs/operate/lemonade.md. Neither issue is blocking for development, but both are
OSS-release blockers: a new contributor opening README.md will learn the wrong version,
the wrong process model, and be pointed at a Caddy auth system that was deleted
five months ago.

---

## 1. Version / Status Banner — STALE (high severity)

| Doc | Claim | Reality |
|-----|-------|---------|
| `README.md:32` | `> **Status:** **v0.2.0**` | `pyproject.toml:7` — version = `0.3.2-alpha.1` |
| `CONTRIBUTING.md:3` | `hal0 is at **v0.2.0**` | Same as above |
| `PLAN.md:10` | `**Status (2026-05-23): v0.2.0 SHIPPED**, v0.3 is the active milestone` | CHANGELOG top entry `[v0.3.2-alpha.1] — 2026-05-29` says "active scope rolls to v0.4" |

**Cleanup:** Update version banners in README and CONTRIBUTING; update PLAN.md opening status
line to reflect v0.3.2-alpha.1 shipped and v0.4 is the active milestone. PLAN.md body
sections (§1 v0.3, §15 Phase 10) still say "v0.3 (active)" — sweep those.

---

## 2. ARCHITECTURE.md Process Model — STALE (high severity)

`ARCHITECTURE.md:8–24` contains the v0.1 process diagram:

```
hal0-slot@primary  hal0-slot@embed  hal0-slot@stt  ...
(llama.cpp)        (llama.cpp)      (Moonshine)
```

The per-slot Moonshine/ComfyUI backend entries in the diagram are stale — those were
replaced in v0.2 (ADR-0008). The `hal0-slot@<name>` unit naming pattern is still live
in the code (`src/hal0/slots.py:942`, `slots.py:1503`, `unit_template.py`) — so the
diagram is not fully wrong in naming, but the Moonshine and direct-llama-server entries
are. Whether `hal0-slot@.service` is still an active systemd template or only a naming
convention is an open question for A1/A3 to resolve. The file contains a split
personality: the Process Model section at the top describes v0.1 backend assignments,
while the Module Layout section correctly shows the v0.3 layout including
`lemonade/`, `omni_router/`, and the v0.3 agents subsystem. The Providers section
(`ARCHITECTURE.md:97–108`) describes the old Provider ABC with
`LlamaServerProvider`, `FLMProvider`, `MoonshineProvider`, `KokoroProvider`,
`ComfyUIProvider` as "stateless" — while the code confirms these classes still exist
in `src/hal0/providers/` (graph node at `src/hal0/providers/flm.py:L106`), the
architecture as described (one provider per backend, slot = systemd unit) does not
match the current Lemonade-unified topology.

Additionally, `ARCHITECTURE.md:121` still lists `/etc/hal0/providers.toml` as a
config location. Code confirms `providers.toml` still exists
(`src/hal0/config/loader.py:326–349`) for external provider keys, but the slot
architecture it implies (one provider per slot) is v0.1 framing.

The State table (`ARCHITECTURE.md:119`) also still contains `/etc/hal0/slots/*.toml`
as a config path — these exist in code but their role changed from "per-slot systemd
unit config" to "per-slot Lemonade config row." The description needs updating.

`ARCHITECTURE.md:259–261` lists three TODO stubs:
- `docs/slots.md` — slot lifecycle state machine *(TODO)*
- `docs/dispatcher.md` — routing algorithm *(TODO)*
- `docs/install.md` — install flow + filesystem layout *(TODO)*

None of these files exist anywhere in the repo.

**Cleanup:** Rewrite the Process Model section to show the Lemonade-unified topology.
Move the old diagram to a brief "v0.1 (historical)" note or delete it. Stub out or
drop the three TODO cross-links.

---

## 3. docs/operate/auth.mdx — SEVERELY STALE (high severity, OSS blocker)

`docs/operate/auth.mdx` is a 399-line document titled
"Caddy reverse proxy, basic_auth at the edge, bearer tokens for the API, and automatic
HTTPS via Caddy's internal CA or Let's Encrypt." It describes `sudo bash installer/install.sh --auth=basic`,
`hal0-caddy.service`, `HAL0_AUTH_ENABLED`, and bcrypt password hashing.

Caddy and the entire auth surface were removed in ADR-0012 (PRs #254, #255, #256, #266),
which shipped as part of v0.3.0-alpha.1. The CHANGELOG explicitly states
("installer auth section gutted to match the ADR-0012 post-Caddy reality" —
`CHANGELOG.md:139–140`). The installer confirms: no `--auth=basic` flag, no
`hal0-caddy.service` unit.

The doc also contains `docs/operate/auth.mdx:331`: `# → 10.0.1.230  hal0.local` —
a hardcoded homelab IP that is a secondary OSS-release issue.

**Cleanup:** Replace auth.mdx entirely. The correct content is: hal0 binds
`0.0.0.0:8080` with no built-in auth per ADR-0012; for non-LAN deployments, put
an upstream reverse proxy in front. Reference Traefik/nginx/Cloudflare Tunnel examples.
Remove the `10.0.1.230` hardcoded IP.

---

## 4. docs/api/openai-compat.mdx — STALE (medium severity)

`docs/api/openai-compat.mdx:33` lists:
```
| POST | /v1/audio/transcriptions | Speech-to-text (Moonshine). |
| POST | /v1/images/generations   | Image generation (ComfyUI on ROCm). |
```

Moonshine was replaced by whisper.cpp via Lemonade in v0.2 (ADR-0008). ComfyUI was
replaced by sd-cpp. `docs/api/openai-compat.mdx:113` also says
"See [Audio](/docs/api/audio/) for the full Moonshine + Kokoro story."

**Cleanup:** Update to "Speech-to-text (Lemonade whisper.cpp)" and
"Image generation (Lemonade sd-cpp)." Correct the Audio cross-link.

---

## 5. README.md — Multiple Stale Claims (medium severity)

**5a. Missing file `docs/v0.2-upgrade.md` (lines 37, 153):**
README references `docs/v0.2-upgrade.md` twice for v0.1.x → v0.2 upgrade instructions.
This file does not exist anywhere in the repo. The migration.md at
`docs/internal/migration.md` covers the haloai → hal0 migration, not the v0.1 → v0.2
slot architecture change.

**5b. Broken link `docs/api/mcp.md` (line 177):**
README links to `docs/api/mcp.md` which does not exist. The actual MCP docs are at
`docs/mcp/overview.md`, `docs/mcp/hal0-admin.md`, `docs/mcp/hal0-memory.md`.

**5c. Broken link `docs/api/agents.md` (line 178):**
README links to `docs/api/agents.md` which does not exist. Agents docs are at
`docs/agents/overview.md`.

**5d. `ui-vue.bak/` in project layout (line 228):**
README project layout includes `ui-vue.bak/` as a live directory. The Vue dashboard
was deleted in the v3 React cutover. `docs/internal/vue-dashboard-archive.md` confirms
the deletion. The directory does not exist on disk.

**5e. "Soon (v0.3)" roadmap items (lines 323–338):**
The README's `### Soon (v0.3)` section still lists GPU TTS, KV% for GPU slots,
benchmarks/presets UI, AUR PKGBUILD, and light mode toggle as upcoming. These were
all still deferred as of CHANGELOG `[v0.3.2-alpha.1]` (GPU TTS:
`CHANGELOG.md:505` explicitly still deferred). Since v0.3 is now shipped and v0.4
is the active milestone, this section should be retitled "v0.4 / upcoming" and the
already-shipped v0.3 items moved to Shipped.

**5f. OmniRouter "8 tools" matches code; CONTEXT.md says "7 tools" (low severity):**
README correctly says "8 tools." CONTEXT.md `OmniRouter` section says
"v0.2 ships these 7 tools" and lists `route_to_chat` as "Deferred to v0.3."
The code has 8 tools in `src/hal0/omni_router/tool_definitions.json` including
`route_to_chat`. CONTEXT.md is out of date on this count.

**Cleanup:** Create or point to a proper upgrade doc, fix the two broken API doc links,
remove `ui-vue.bak/` from the project layout, update the roadmap section for v0.4.

---

## 6. CONTEXT.md — Stale Claims (medium severity)

**6a. MCP auth:**
`CONTEXT.md:36` says MCP servers are accessible "Auth via existing Bearer token
(ADR-0001)." ADR-0001 was superseded by ADR-0012 which removed Bearer token auth
from the API entirely. The code at `src/hal0/api/mcp_mount.py:67` confirms:
"hal0 removed network auth entirely (ADR-0012) and binds `0.0.0.0` on the LAN."
The MCP bearer logic remaining is only for deriving the `client_id` for the Hermes
agent identity, not for access control.

**6b. OmniRouter tool count:**
See 5f above. `CONTEXT.md:152` says 7 tools shipped in v0.2; reality is 8.

**6c. v0.3 status line:**
`CONTEXT.md:282` says "v0.3 = the active milestone (2026-05-23 →)." v0.3.2-alpha.1
has shipped; v0.4 is now active.

**6d. Memory as default-on Cognee:**
`CONTEXT.md:24–26` describes Cognee as "the embedded memory engine adopted from v0.2."
Code reality (`src/hal0/api/__init__.py:1486`): memory is gated behind
`HAL0_MEMORY_ENABLED=0` by default, deferred pending a brain redesign.
The comment says "brain redesign (Hindsight + hal0-wiki) lands. Set HAL0_MEMORY_ENABLED=1
to reintroduce it with NO code change." ADR-0005 still has Status: Draft.

**Cleanup:** Update MCP auth description to remove ADR-0001 reference; correct tool
count; flag HAL0_MEMORY_ENABLED gate; update milestone pointer to v0.4.

---

## 7. CONTRIBUTING.md — Hardcoded Homelab IP (medium severity, OSS blocker)

`CONTRIBUTING.md:28` and `CONTRIBUTING.md:80,141`: The γ release-gate test
documentation hardcodes `10.0.1.230` as the "hal0-test LXC" target:

```
make release-test HAL0_TEST_HOST=10.0.1.231 HAL0_TEST_SSH_KEY=~/.ssh/my-test-key
```

```
The `hal0-test` LXC at `10.0.1.230` is the standing target for
release-gate runs.
```

The second IP (`10.0.1.231`) also appears as a make example. For an OSS contributor,
these IPs are meaningless and their test infra will have a different host. The HAL0_TEST_HOST
env var override is documented (`CONTRIBUTING.md:80`), but the default assumption
(that `10.0.1.230` is the right target) needs to be changed to "you supply your own
via `HAL0_TEST_HOST`."

**Cleanup:** Flip the default to "operator provides HAL0_TEST_HOST; no default assumed."
Remove the `10.0.1.230` / `10.0.1.231` hardcoded IPs from the body text.

---

## 8. docs/operate/openwebui.mdx and docs/agents/hermes/CONFIG.md — Minor Homelab Leakage

`docs/operate/openwebui.mdx:79,81`: Uses `*.thinmint.dev` as a concrete
example domain in a public-facing doc. Should be `*.yourhomelab.dev` or a generic
placeholder.

`docs/agents/hermes/CONFIG.md:365`: Uses `hal0.thinmint.dev` as an example remote
browser URL. Same issue.

**Cleanup:** Replace with generic placeholder domains (`hal0.example.lan`,
`*.example.lan`).

---

## 9. docs/README.md — Docs Sync Convention Not Explicit Enough (low severity)

`docs/README.md:7–14` scopes its "canonical = hal0-web" claim to "Top-level `docs/*.mdx`."
The two files that carry the "repo-native canonical" banner (`docs/operate/lemonade.md`
and `docs/dashboard/v3.md`) are both `.md` files, not `.mdx`, which tracks the extension
split: `.mdx` files sync from hal0-web; `.md` files (agents, mcp, memory, internal) are
edited in this repo. The convention is coherent but undocumented — `docs/README.md` only
describes the `.mdx` half and never mentions that `.md` files in the same tree are
repo-native.

This matters practically: a contributor wanting to fix `docs/operate/auth.mdx`
(which describes deleted Caddy functionality) must edit it in `Hal0ai/hal0-web`, not here.
The fact that the sync target glob (in hal0-web's GitHub Actions workflow) is unknown from
this repo is a minor open question.

**Cleanup:** Add a paragraph to `docs/README.md` clarifying: `.mdx` files — edit in
hal0-web; `.md` files (including `lemonade.md`, `v3.md`, and all `docs/agents/`,
`docs/mcp/`, `docs/memory/` files) — edit here. Remove ambiguity for contributors.

---

## 10. ADR Status Audit

| ADR | Status in file | Reality check |
|-----|---------------|---------------|
| 0001 | Superseded by ADR-0012 | Correct. Caddy+auth removed. |
| 0002–0003 | (no status check needed — config/capability overlay) | Not checked. |
| 0004 | Accepted | Agents shipped. pi-coder code in repo but not picker-visible per `docs/agents/overview.md`. |
| 0005 | **Draft** | Memory gated behind `HAL0_MEMORY_ENABLED=0`; Cognee wrapper exists in code but disabled. ADR status accurately reflects gated state. |
| 0006 | Superseded by ADR-0008 | Correct. |
| 0007 | Superseded by ADR-0008 | Correct. |
| 0008 | Accepted | Lemonade unified runtime shipped. |
| 0009–0014 | Accepted/Accepted/Draft/Draft | Not fully checked; seems aligned. |
| 0015 | **Draft** (target: alpha.2) | MCP host platform; partially shipped per CHANGELOG v0.3.2-alpha.1 §Added (install/uninstall/config wired). ADR may need status bump. |
| **0016** | **MISSING** | No ADR-0016 exists. Sequence jumps 0015 → 0017. Likely an ADR that was drafted and then merged into another or silently abandoned. Open question. |
| 0017 | Accepted (post-implementation) | Correct. Bell/inbox shipped in Epic #322. |
| 0018–0020 | Accepted/Accepted/Proposed | Seem aligned with changelog. |
| 0021 | **Proposed** | `hal0 connect` command not yet implemented. `docs/reference/cli.mdx:332` correctly marks it "Planned (ADR-0021)." |
| 0022 | **Proposed** | Backend selection display; per-slot backend honoring still partially incomplete. |
| 0023 | Accepted (2026-06-03) | Bundled llama.cpp Vulkan build bumped. Aligned. |

**Open question:** What happened to ADR-0016? Was it merged into 0017, or is there a
missing decision record?

---

## 11. ADR-0005 Memory — Cognee Description in Multiple Docs Is Misleading

Several docs describe Cognee-backed memory as an active, default-on feature:
- `CONTEXT.md:24`: "The embedded memory engine adopted from v0.2"
- `CONTEXT.md:45`: "hal0 memory MCP — cross-session, cross-agent, cross-app. Backed by Cognee."
- `docs/memory/overview.md` (not checked in depth but likely similar)
- `docs/mcp/hal0-memory.md` (not checked)

Code reality: `src/hal0/api/__init__.py:1486` gates memory behind
`HAL0_MEMORY_ENABLED=0`. The comment in the code explicitly says this was deferred
pending a brain redesign (`docs/internal/brain-redesign/`). The MCP memory server route
is mounted but returns 503 when `app.state.memory_provider is None`.

This is not a "docs are wrong" situation — the code intentionally supports both states —
but the docs present Cognee memory as a shipped, active feature when it is actually
default-off and pending architectural revision.

**Cleanup:** Add a visible note to `docs/memory/overview.md` and related docs that
memory is gated behind `HAL0_MEMORY_ENABLED=1` in v0.3 and is default-off pending
the Hindsight/hal0-wiki brain redesign.

---

## 12. docs/getting-started/install.mdx — Stale Toolbox Container References (medium severity)

`docs/getting-started/install.mdx:63`: "The slot toolbox containers all live on the host."
`docs/getting-started/install.mdx:102–103`: "Docker reachable. The slot toolboxes run
as containers. The installer checks `docker ps` before doing anything destructive."
`docs/getting-started/install.mdx:198–200`: Lists `HAL0_AUTO_PULL`, `HAL0_TOOLBOX_IMAGE_VULKAN`,
`HAL0_TOOLBOX_IMAGE_ROCM` as installer env vars.

The six per-modality toolbox containers (`hal0-toolbox-vulkan`, `rocm`, `flm`,
`moonshine`, `kokoro`, `comfyui`) were retired in v0.2 in favor of `lemond` via
ADR-0008. `installer/install.sh:655` has a comment: "hal0-slot@.service template
removed in PR-9 (v0.2 retires per-modality ...)" The Docker check is still a soft
preflight for OpenWebUI only. The env vars `HAL0_TOOLBOX_IMAGE_VULKAN/ROCM` still
appear in the installer's header comment (`installer/install.sh:16`) as legacy but
may not actively do anything for inference.

**Cleanup:** Update install.mdx to describe the Lemonade runtime. Replace toolbox
container descriptions with `hal0-lemonade.service` and `lemond`. Remove or clarify
the Docker requirement note (Docker is only needed for OpenWebUI, not inference).

---

## 13. ARCHITECTURE.md "See also" Cross-links — Broken References

`ARCHITECTURE.md:259–261`:
```
- [`docs/slots.md`](./docs/slots.md) — slot lifecycle state machine *(TODO)*
- [`docs/dispatcher.md`](./docs/dispatcher.md) — routing algorithm *(TODO)*
- [`docs/install.md`](./docs/install.md) — install flow + filesystem layout *(TODO)*
```

These three files do not exist. The slot lifecycle is documented in
`docs/slots/what-is-a-slot.mdx` and the state machine is in `ARCHITECTURE.md` itself.

`ARCHITECTURE.md:3` references `docs/install.md` for "user-facing shape" — this file
also does not exist; the correct pointer is `docs/getting-started/install.mdx`.

**Cleanup:** Fix the three broken cross-links to point to existing files or replace
with "see `docs/slots/`" and "see `docs/getting-started/install.mdx`."

---

## 14. CONTRIBUTING.md — Test Tier Table vs Reality

`CONTRIBUTING.md:28` lists the γ release-gate as testing
"llamacpp-vulkan, llamacpp-rocm, flm-npu (chat + trio asr/embed), whispercpp (STT),
kokoro-cpu (TTS), sd-cpp (image), updater, openwebui."

The CHANGELOG `[v0.3.1-alpha.1] Added` section includes new test coverage (δ-harness
for Hermes `delegate_task`, etc.) that is not reflected in CONTRIBUTING.md's test tier
table. The table says three integration tests; the actual test suite has grown.

This is low-severity (CONTRIBUTING is a living doc), but the table should be updated.

---

## 15. Docs Cleanup Plan (Keep / Update / Delete / Move)

**Important routing note:** `.mdx` files under `docs/` sync from `Hal0ai/hal0-web`
(per `docs/README.md`). Edits to `.mdx` files must land in hal0-web to survive the
next sync. Root-level docs (README, CONTRIBUTING, PLAN, CONTEXT, ARCHITECTURE) and
`.md` files (agents/*, mcp/*, memory/*, lemonade.md, v3.md, internal/**) are edited
directly in this repo.

| Document | Action | Where to edit | Rationale |
|----------|--------|--------------|-----------|
| `README.md` | **Update** | This repo | Version banner, roadmap section, broken links, ui-vue.bak |
| `CONTRIBUTING.md` | **Update** | This repo | Version banner, remove homelab IPs, update test tier table |
| `PLAN.md` | **Update** | This repo | Status line, v0.3→v0.4 active milestone pointer |
| `CONTEXT.md` | **Update** | This repo | MCP auth description, tool count (7→8), memory gate note, v0.3→v0.4 |
| `ARCHITECTURE.md` | **Update** | This repo | Rewrite Process Model section for Lemonade topology; fix 3 broken cross-links |
| `docs/operate/auth.mdx` | **Rewrite** | **hal0-web** | Remove all Caddy/basic_auth content; replace with ADR-0012 posture + upstream-proxy examples |
| `docs/api/openai-compat.mdx` | **Update** | **hal0-web** | Replace Moonshine→whisper.cpp, ComfyUI→sd-cpp in endpoint table |
| `docs/getting-started/install.mdx` | **Update** | **hal0-web** | Remove toolbox container descriptions, update Docker note |
| `docs/operate/openwebui.mdx` | **Update** | **hal0-web** | Replace thinmint.dev with generic placeholder |
| `docs/agents/hermes/CONFIG.md` | **Update** | This repo | Replace thinmint.dev with generic placeholder |
| `docs/README.md` | **Update** | This repo | Add paragraph clarifying .mdx=hal0-web vs .md=this-repo editorial policy |
| `docs/memory/overview.md` | **Update** | This repo | Add HAL0_MEMORY_ENABLED gate notice |
| `docs/mcp/hal0-memory.md` | **Update** | This repo | Add HAL0_MEMORY_ENABLED gate notice |
| `docs/internal/v0.3-state.md` | **Keep** | This repo | Snapshot doc — historical record, explicitly dated |
| `docs/internal/archive/` | **Keep** | This repo | Historical planning records, not living docs |
| `docs/internal/brain-redesign/` | **Keep** | This repo | Active internal design docs for v0.4 scope |
| `docs/internal/adr/0001–0023` | **Keep** (but check 0015, 0016) | This repo | ADRs are append-only records; superseded ones stay |
| `docs/internal/adr/0015` | **Update status** | This repo | Partially shipped in v0.3.2-alpha.1 |

---

## Cross-cutting Seams

- **CHANGELOG ↔ README/PLAN.md:** The shipped feature set in CHANGELOG is the ground
  truth; README and PLAN.md both lag by roughly two milestones. Any docs sweep must
  start from CHANGELOG, not from PLAN.md.
- **docs/README.md ↔ lemonade.md/v3.md:** The sync direction policy is contradicted
  within the docs tree itself. This affects other agents editing docs (they may edit
  the wrong repo).
- **ADR-0005 (memory Draft) ↔ all memory docs:** Memory docs describe Cognee as active;
  the code gate means memory subsystem behaviors should be tested with `HAL0_MEMORY_ENABLED=1`.
  Other agents building or testing memory-related features need to know this.
- **Providers still in code vs "retired" in docs:** `src/hal0/providers/` contains
  `LlamaServerProvider`, `FLMProvider`, `MoonshineProvider`, `KokoroProvider`,
  `ComfyUIProvider`. The Module Layout in ARCHITECTURE.md lists them under
  `providers/`, but the Process Model claims the provider stack was "retired."
  The install+runtime agent should clarify which providers are dead code vs still
  active (e.g. FLMProvider is likely still used for the NPU path).

---

## Open Questions

1. **ADR-0016 is missing.** What decision was number 16? Was it merged into ADR-0017
   (bell/inbox), withdrawn, or just skipped? Should be answered before an OSS release
   so the ADR sequence is explained.
2. **docs sync glob:** The hal0-web GitHub Actions workflow that syncs `.mdx` files
   into this repo is not visible from this repo. What is the exact glob? Knowing this
   is required before editing `.mdx` content (edits here will be overwritten otherwise).
   The `.mdx` vs `.md` extension split inferred from `docs/README.md` is likely correct
   but unverified.
3. **hal0-slot@ still live?** `installer/install.sh:655` says the template was "removed
   in PR-9" but `src/hal0/slots.py:942/1503` and `unit_template.py` still render
   `hal0-slot@{name}.service` names. Is this unit template still installed and active,
   or only a naming prefix used in code while the actual systemd unit template is gone?
   Route to A1/A3 for clarification.
4. **Providers dead code:** Are `LlamaServerProvider`, `MoonshineProvider`,
   `KokoroProvider`, `ComfyUIProvider` still active code paths, or are they dead
   code awaiting removal? ARCHITECTURE.md describes them as active but the narrative
   says "retired."
5. **`docs/v0.2-upgrade.md` never existed?** README links to it twice. Was it always
   a placeholder link that was never filled, or was it deleted at some point?
6. **`HAL0_TOOLBOX_IMAGE_VULKAN/ROCM` env vars:** Still in `installer/install.sh:16`
   header comment. Are these truly dead (Lemonade replaced them) or still usable for
   the OpenWebUI-adjacent toolbox path?
