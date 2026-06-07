# OPEN QUESTIONS for the user — hal0 audit (synthesis, 2026-06-07)

> Grouped by theme. Each lists the originating agent(s) and what the answer unblocks.
> Several were raised independently by multiple agents — those are the highest-value to
> resolve. Where synthesis-grep already settled a sub-question, the resolved fact is noted.

---

## 1. Dead-code rulings (block doc + code consistency)

**1.1 — Are the non-lemonade providers dead?** (A1, A2, B1, B2 — four agents)
`LlamaServerProvider / MoonshineProvider / KokoroProvider / ComfyUIProvider` sit in
`providers/__init__.py:47`. The hot path dispatches only via `lemonade_provider()`.
*Synthesis grep partially answers:* they ARE reachable via `get_provider` (`v1.py:1026`,
`unit_template.py:102`) and `SELF_MANAGED_PROVIDERS = {kokoro, moonshine, vibevoice}`
(`slots/state.py:124`) treats some as a live non-lemond spawn path. So not cleanly dead.
**Question for you:** is the self-managed (kokoro/moonshine/vibevoice) path an intentionally
supported runtime, or vestigial from v0.1 that should be deleted? This ruling unblocks the
ARCHITECTURE.md providers rewrite and ~1800 lines of potential dead-code removal.

**1.2 — Is `hal0-slot@.service` still a live unit, or naming-only?** (A1/A3 via B2, raised twice)
`installer/install.sh:655` says the base template unit was removed in PR-9, but
`slots/unit_template.py` still renders `hal0-slot@<name>.service.d/override.conf` drop-ins and
the naming survives across the code. *Synthesis grep:* found no runtime caller of the renderer
in `manager.py`/`routes/slots.py`/`slot_commands.py` in a single pass — but did not exhaustively
trace. **Question:** is `unit_template.py` an orphaned renderer (base unit gone, nothing renders
the drop-in at runtime), or does some path still install per-slot drop-ins? If orphaned, it's a
clean deletion.

**1.3 — `pi_coder` lifecycle.** (B1) Still in `BUNDLED_AGENTS` (`agents/manager.py:88`) and
referenced 14+ places, but dropped from v0.2/v0.3 promo. Keep for v0.4, or strip now?

**1.4 — FeatureFlags Phase-1 port: alive or abandoned?** (B1) `config/features.py` stubs all
raise `NotImplementedError("Phase 1: port from /opt/haloai/lib/features.py")`. Is there a
tracking issue, or was the haloai port quietly dropped? Determines whether it's deleted or filled.

**1.5 — FirstRunWizard stub reachability.** (B1 ↔ A3) `installer/wizard.py:71,96` raise
NotImplementedError, but A3 found the real first-run OTP/`.first-run.lock` lives in
`routes/installer.py`, not the wizard. Is the wizard a dead parallel path (delete), or wired
behind a firstrun flag not yet set?

---

## 2. Routing architecture (one reconcile, multiple owners)

**2.1 — How should routing be consolidated given omni_router depends on `route_for_request`?**
(A1's proposal vs verified omni_router reality) A1 wants the Dispatcher as sole routing
authority and proposes demoting `SlotManager.route_for_request` + deleting legacy
`proxy.resolve_slot`. *Synthesis grep:* `route_for_request` is the omni-router's routing
primitive (`omni_router/dispatch.py:127`, `filter.py:121`) and `app.state.omni_router` is live
(`api/__init__.py:1100`); `resolve_slot` is still reached (`router.py:549`). So a blind demote
breaks omni-router. **Question:** should omni_router be folded under the Dispatcher (one
authority), or is the omni_router → `route_for_request` an intentional second routing tier for
multi-modal/label matching that should stay? This is the biggest architectural decision in the
backlog.

---

## 3. Auth model (denied by docs, scattered in code)

**3.1 — Is the three-path auth surface intentional?** (A2, B2, B3) ADR-0012 docs say auth was
removed, yet `deps.py` (token/writer), `api/agents/_auth.py` (HMAC WS), and `mcp_mount.py`
(bearer) all live. **Question:** is this the intended post-ADR-0012 model (LAN-open API + WS HMAC
for the agent chat + MCP bearer for identity), and should it be documented as such — or should
these fold into one gate? And: is `thinmint.dev` in `_auth.py:61` `DEFAULT_ALLOWED_ORIGINS` an
oversight (treat as bug) or a deliberate demo default (move behind an env var)?

**3.2 — root + 0.0.0.0:8080, no auth: acceptable OSS posture?** (A3) `hal0-api` runs as root on
all interfaces with no gate by default (`install.sh:160,640,643`). Intended for the LAN dev box,
but is this the shipped default for arbitrary OSS installs, or should the installer default to a
non-root user / loopback bind / opt-in exposure?

---

## 4. Durability & install traps (decide the policy)

**4.1 — Where does the editable-install update refusal belong?** (A3) `hal0 update` on editable
`/opt/hal0` silently no-ops; neither CLI, route, nor `Updater.apply()` refuse. Should the hard
refusal go in `Updater.apply()` (one chokepoint), or in the CLI + route (user-facing, leaves
apply() pure)?

**4.2 — Is model-pull job loss-on-restart acceptable?** (A2) Updater jobs are disk-mirrored;
model-pull jobs are not (`models.py:1230`). Extend the mirror, or accept the gap?

**4.3 — pgvector silent data loss in degrade mode.** (B1, ties to memory) When memory is enabled
but Hindsight is unavailable, `pgvector_provider` keeps writes in memory and drops them on
restart. Known/accepted gap, or should it loudly warn / refuse writes? (See BACKLOG 0.5.)

**4.4 — On a Lemonade-skipped install, what should the dashboard show?** (A3) If the placeholder
SHA skips Lemonade (or the tarball download fails non-fatally), should SlotManager/dashboard
surface a distinct "inference unavailable" state instead of chat just timing out?

---

## 5. Docs infrastructure & history

**5.1 — Exact hal0-web `.mdx` sync glob?** (B2, raised by multiple) `.mdx` files sync from
`Hal0ai/hal0-web` via a GitHub Actions workflow not visible from this repo. The exact glob is
needed before mass-editing `.mdx` — edits here are otherwise overwritten. What is it?

**5.2 — What happened to ADR-0016?** (B2) Sequence jumps 0015 → 0017. Withdrawn, merged into
0017, or skipped? Should be explained before OSS so the ADR sequence is legible.

**5.3 — Did `docs/v0.2-upgrade.md` ever exist?** (B2, B3) README links it twice; it's nowhere in
the repo. Always a dead placeholder, or deleted? Determines create-vs-remove-link.

**5.4 — `HAL0_TOOLBOX_IMAGE_VULKAN/ROCM` env vars: dead?** (B2) Still in `install.sh:16` header.
Truly replaced by Lemonade, or still usable for an OpenWebUI-adjacent toolbox path?

---

## 6. OSS-release scope decisions

**6.1 — `docs/internal/` tree: gitignore entirely, or only `/archive/`?** (B3) The ADRs and
brain-redesign docs seem intended to be public; the archive + handoff notes leak lab IPs. Where's
the line?

**6.2 — `scripts/import_haloai_models.py` (decommissioned CT220): archive, update, or remove?**
(B3)

**6.3 — Contribution model & CLA.** (B3) `CONTRIBUTING.md` still says external PRs aren't merged.
When does that flip, and is a CLA/DCO wanted before the merge window opens? Also: add
`CODE_OF_CONDUCT.md` + `SECURITY.md` (currently absent at root).
