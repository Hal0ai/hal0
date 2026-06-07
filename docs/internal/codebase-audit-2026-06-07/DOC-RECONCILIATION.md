# DOC-RECONCILIATION — hal0 (synthesis, 2026-06-07)

> Consolidated doc-vs-code drift from B2 (doc-reconciliation), cross-checked against
> A1/A2/A3/B1/B3 code findings, with a keep / update / delete / merge / rewrite plan.
>
> **Editing routing (critical):** `.mdx` files under `docs/` sync FROM `Hal0ai/hal0-web`
> via a GitHub Actions workflow — edits to `.mdx` here are overwritten on next sync, so
> they must land in hal0-web. Root docs (README, CONTRIBUTING, PLAN, CONTEXT, ARCHITECTURE)
> and `.md` files (`docs/agents/`, `docs/mcp/`, `docs/memory/`, `lemonade.md`, `v3.md`,
> `docs/internal/**`) are edited in THIS repo. The exact sync glob is unverified — see
> QUESTIONS before mass-editing `.mdx`.

---

## The two synthesis stories (drift that spans docs AND code)

### S1. "Auth was removed" is the load-bearing false claim
B2 flagged `auth.mdx` (399 lines of deleted Caddy/basic_auth) and `CONTEXT.md:36`
(ADR-0001 bearer tokens) as stale-removed. But A2 found **three live auth code paths** still
in the tree, and B3 found one of them (`api/agents/_auth.py:61`) ships `thinmint.dev` as a
default. So the docs are not just "stale about a removed feature" — they actively *deny a
security surface that still exists and leaks a private domain*. The doc rewrite and the code
fix (BACKLOG 0.1) must land together: `auth.mdx` should describe the post-ADR-0012 posture
(`0.0.0.0:8080`, no edge auth) **plus** the residual WS-HMAC + MCP-bearer paths, not pretend
auth is wholly gone.

### S2. Memory docs describe a default-on feature that is dark + lossy
B2: docs (`CONTEXT.md:24`, `docs/memory/overview.md`, `docs/mcp/hal0-memory.md`) present Cognee
memory as active/default-on. A2: code gates it behind `HAL0_MEMORY_ENABLED=0`; every surface
degrades to no-op/503. B1: the degrade fallback drops writes silently on restart. The doc fix
(add the gate notice) is necessary but **insufficient** on its own — pair it with BACKLOG 0.5
(warn on lossy writes) so the doc doesn't tell users to flip a flag that leads to silent loss.

---

## Drift inventory + plan

| Doc | Drift (code reality) | Action | Edit where | Sev |
|---|---|---|---|---|
| `README.md:32` | Status `v0.2.0` vs pyproject `0.3.2-alpha.1` | **Update** banner | this repo | high |
| `README.md:36,153,177,178` | links `docs/v0.2-upgrade.md`, `docs/api/mcp.md`, `docs/api/agents.md` — none exist | **Delete/fix** links (real: `docs/mcp/`, `docs/agents/overview.md`) | this repo | med |
| `README.md:228` | lists `ui-vue.bak/` (deleted in v3 cutover) | **Delete** from layout | this repo | low |
| `README.md:323–338` | "Soon (v0.3)" roadmap; v0.3 shipped, v0.4 active | **Update** → v0.4 / move shipped items | this repo | med |
| `CONTRIBUTING.md:3` | version `v0.2.0` | **Update** | this repo | med |
| `CONTRIBUTING.md:28,80,141` | hardcoded `10.0.1.230/.231` release-gate target | **Update** → operator-supplied `HAL0_TEST_HOST`, no default | this repo | high (OSS) |
| `CONTRIBUTING.md:28` | test-tier table understates grown suite | **Update** | this repo | low |
| `PLAN.md:10` + §1/§15 | `v0.2.0 SHIPPED, v0.3 active` | **Update** → v0.3 shipped, v0.4 active | this repo | high |
| `CONTEXT.md:36` | MCP "Auth via Bearer (ADR-0001)" — removed by ADR-0012 (bearer now only derives client_id) | **Update** | this repo | med |
| `CONTEXT.md:152` | OmniRouter "7 tools"; code has 8 (`route_to_chat` shipped) | **Update** | this repo | low |
| `CONTEXT.md:24–26,282` | Cognee default-on; v0.3 active | **Update** (see S2) + v0.4 | this repo | med |
| `ARCHITECTURE.md:8–24` | Process Model = v0.1 per-slot Moonshine/llama topology | **Rewrite** for Lemonade-unified | this repo | high |
| `ARCHITECTURE.md:97–108,119,121` | Providers/state described as v0.1 one-provider-per-slot; lists `providers.toml`, `slots/*.toml` with old roles | **Update** descriptions (files exist, roles changed) | this repo | med |
| `ARCHITECTURE.md:259–261,3` | 4 broken cross-links (`docs/slots.md`, `docs/dispatcher.md`, `docs/install.md`) | **Delete/redirect** to `docs/slots/`, `docs/getting-started/install.mdx` | this repo | low |
| `docs/operate/auth.mdx` | 399 lines of deleted Caddy/basic_auth (ADR-0012); `:331` `10.0.1.230` | **Rewrite** (see S1) | **hal0-web** | high (OSS) |
| `docs/api/openai-compat.mdx:33,113` | lists Moonshine STT + ComfyUI image (replaced v0.2 by whisper.cpp / sd-cpp) | **Update** endpoint table | **hal0-web** | med |
| `docs/getting-started/install.mdx:63,102,198` | Docker toolbox containers + `HAL0_TOOLBOX_IMAGE_*` (retired v0.2 ADR-0008) | **Update** → `hal0-lemonade.service`/`lemond`; clarify Docker = OpenWebUI only | **hal0-web** | med |
| `docs/operate/openwebui.mdx:79,81` | `*.thinmint.dev` example domain | **Update** → generic placeholder | **hal0-web** | high (OSS) |
| `docs/agents/hermes/CONFIG.md:365` | `hal0.thinmint.dev` example | **Update** → placeholder | this repo | high (OSS) |
| `docs/memory/overview.md`, `docs/mcp/hal0-memory.md` | Cognee active (see S2) | **Update** → add `HAL0_MEMORY_ENABLED` gate + lossy-degrade note | this repo | med |
| `docs/README.md:7–14` | only documents `.mdx`=hal0-web; never says `.md`=this-repo | **Update** add editorial-policy paragraph | this repo | low |
| `docs/internal/adr/0015` | Status `Draft` but partially shipped v0.3.2-alpha.1 | **Update status** | this repo | low |
| `src/hal0/config/paths.py:150–169` | docstring: `.first-run.lock` "dropped by install.sh" — it's API-side (`routes/installer.py`) | **Update** docstring (A3) | this repo | med |
| `installer/install.sh:169–170` | comment falsely claims updater "refuses apply() in this mode" — it only skips re-pip (A3) | **Update** comment + add real refusal (BACKLOG 2.1) | this repo | med |
| `installer/install.sh:5,10` | header says `/opt/hal0` default; body defaults `/usr/lib/hal0` (A3) | **Update** header | this repo | med |
| `installer/install.sh:16` | header lists `HAL0_TOOLBOX_IMAGE_VULKAN/ROCM` as legacy | **Verify dead** then delete (QUESTIONS) | this repo | low |

---

## Keep (historical / snapshot — NOT drift)

| Doc | Why keep |
|---|---|
| `docs/internal/v0.3-state.md` | dated snapshot — historical record |
| `docs/internal/archive/**` | archived spike runbooks (but sanitise IPs before OSS — BACKLOG 0.7) |
| `docs/internal/brain-redesign/**` | active v0.4 design docs |
| `docs/internal/adr/0001–0023` | ADRs are append-only; superseded ones stay |
| CHANGELOG.md | **ground truth** — any doc sweep starts here, not PLAN.md |

---

## ADR gaps to explain before OSS

- **ADR-0016 MISSING** — sequence jumps 0015 → 0017. Withdrawn? merged into 0017? skipped?
  (QUESTIONS).
- **ADR-0015** Draft but partially shipped (MCP install/uninstall/config wired per CHANGELOG
  v0.3.2-alpha.1) while `routes/mcp.py` still 501s the supervisor actions (A2) — status + code
  both need a pass.

---

## Cross-cut: providers "retired in narrative, present in code"

B2 + A1 + A2 + B1 all touched this. ARCHITECTURE.md Module Layout lists
`LlamaServerProvider/MoonshineProvider/KokoroProvider/ComfyUIProvider` as active while the
Process Model narrative says "retired." **Verified (synthesis grep):** the `PROVIDERS` dict is
populated (`providers/__init__.py:47`) and reachable via `get_provider` (`v1.py:1026`,
`unit_template.py:102`); `SELF_MANAGED_PROVIDERS = {kokoro, moonshine, vibevoice}`
(`slots/state.py:124`) treats some as a live non-lemond spawn path. So they are **not cleanly
dead** — the hot path is lemond-only but residual liveness exists. This needs an owner ruling
(QUESTIONS) before either the doc OR the code can be made consistent. Don't let the doc fix
assert "retired" until the dead-code question is settled.
