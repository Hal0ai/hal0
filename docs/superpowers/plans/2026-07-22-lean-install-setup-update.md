# Lean Install, Setup, and Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace duplicated install/setup/update policy with one validated lifecycle catalog and deep resolver/converger seam that produces a minimal, portable, recoverable hal0 installation.

**Architecture:** Release-owned package, runner, model, profile, and bootstrap facts compile into one immutable catalog. `LifecycleCatalog` resolves plans; `LifecycleConverger` applies them through existing slot, profile, model, updater, database, and system adapters. Shell retains release trust and host bootstrap only; API, CLI, and WebUI render the same plans, readiness issues, actions, and recommendations.

**Tech Stack:** Python 3.12+, Pydantic v2, SQLite, Typer, FastAPI, Bash, systemd, Podman, React 18, TanStack Query, Vitest, Playwright, pytest, Ruff.

## Global Constraints

- Work only in `/home/mint/hal0/.worktrees/v1-rc-critical-path` from design commit `66147eaa` or its reviewed descendant.
- Preserve unrelated dirty `graphify-out/GRAPH_REPORT.md`, `graphify-out/manifest.json`, and Shepherd artifacts.
- `src/hal0/updater/updater.py` remains the only updater.
- Do not add standalone `setup.sh`, `updater.sh`, or `migration.sh`.
- Do not create initial TTS, STT, embed, rerank, image, vision, utility, or FLM slot scaffolds.
- Existing slots never change runner pins automatically.
- Built-in profiles are release-owned and immutable; custom profiles remain operator-owned.
- Runtime resolution uses immutable image digests and immutable Hugging Face revisions/files/checksums.
- Optional artifact failures create durable actionable issues; trust, catalog, permissions, migration, and base-control-plane failures remain fatal.
- Never expose secret values in config, plans, logs, API responses, CLI JSON, errors, or support bundles.
- Tier 1: apt, dnf, and pacman families on bare metal, VM, privileged LXC, and unprivileged LXC. Tier 2: WSL2 with systemd and Podman. Refuse unsupported hosts before mutation.
- Do not edit CI workflows or broadly remove existing tests until `/ci-pr1330-repair/` is complete and integrated.
- Use targeted tests locally; do not run the uncapped full pytest suite on hosts where podman/systemd tests can hang.
- After each code slice, run `graphify update .` and commit only slice-owned files.
- Every slice receives independent spec and quality review before the next writer starts.

## File and Module Structure

### New lifecycle module

- `src/hal0/lifecycle/types.py` — immutable catalog, request, plan, result, issue, action, and recommendation shapes.
- `src/hal0/lifecycle/catalog.py` — catalog loading, validation, indexing, `resolve()`, and `compare()`; this is the primary deep interface.
- `src/hal0/lifecycle/state.py` — transactional SQLite persistence for activation, artifacts, jobs, issues, and recommendations.
- `src/hal0/lifecycle/converger.py` — ordered, idempotent plan application through injected existing-store adapters.
- `src/hal0/lifecycle/host.py` — normalized host facts and apt/dnf/pacman/systemd/container adapters.
- `src/hal0/lifecycle/data/*.toml` — authored package, runner, model, profile, and bootstrap facts.
- `src/hal0/lifecycle/data/catalog.json` — canonical generated runtime catalog.

### Existing modules retained as owners

- `src/hal0/profiles/__init__.py` — profile interface and built-in/custom ownership.
- `src/hal0/runners/__init__.py` — compatibility facade over lifecycle catalog during migration.
- `src/hal0/slot_config/__init__.py` and `src/hal0/slots/interface.py` — slot persistence/application.
- `src/hal0/registry/` — model inventory and pull implementation.
- `src/hal0/updater/updater.py` — release preparation, commit, and rollback.
- `src/hal0/db/` — SQLite connection and migration runner.
- `installer/bootstrap.sh`, `installer/install.sh`, `installer/uninstall.sh` — trust and OS integration shells.

---

### Task 0: Reconcile the Execution Baseline and CI Ownership

**Files:**
- Read: `docs/rework/ci-pr1330-repair-coordination-note.md`
- Read: `docs/superpowers/specs/2026-07-22-lean-install-setup-update-design.md`
- Create: `.superpowers/sdd/progress.md`

**Interfaces:**
- Consumes: final `/ci-pr1330-repair/` head when available.
- Produces: recorded baseline, CI ownership gate, per-task commit/test/review ledger.

- [ ] **Step 1: Record the current isolated baseline**

Run:

```bash
cd /home/mint/hal0/.worktrees/v1-rc-critical-path
git status --short --branch
git rev-parse HEAD
git log -3 --oneline --decorate
```

Expected: branch `work/v1-rc-critical-path`; design commit `66147eaa` is present; only known graphify files may be dirty.

- [ ] **Step 2: Determine whether CI repair is integrated**

Run:

```bash
git log --all --oneline --decorate --grep='ci-pr1330\|PR #1330 CI\|repair CI' -20
```

If the CI owner has not supplied a final head, record `CI_WIRING_BLOCKED=yes`. Tasks 1–14 may add focused tests but must not edit `.github/workflows/` or delete broad suites.

- [ ] **Step 3: Create the progress ledger**

Write:

```markdown
# Lean Lifecycle SDD Progress

Design: docs/superpowers/specs/2026-07-22-lean-install-setup-update-design.md
Plan: docs/superpowers/plans/2026-07-22-lean-install-setup-update.md
Baseline: 66147eaa
CI repair integrated: no

- [ ] Task 1: catalog
- [ ] Task 2: resolver
- [ ] Task 3: built-in profiles
- [ ] Task 4: lifecycle state and converger
- [ ] Task 5: host adapters
- [ ] Task 6: installer essentials
- [ ] Task 7: minimal base convergence
- [ ] Task 8: readiness and retries
- [ ] Task 9: Hermes brain
- [ ] Task 10: updater integration
- [ ] Task 11: enable/disable
- [ ] Task 12: runner UX
- [ ] Task 13: integration secrets
- [ ] Task 14: uninstall/reinstall
- [ ] Task 15: CI consolidation
```

- [ ] **Step 4: Keep the ledger as session recovery state**

```bash
test -f .superpowers/sdd/progress.md
git status --short -- .superpowers/sdd/progress.md
```

Expected: the ledger exists and remains untracked/ignored session state. Do not commit it; append each reviewed task immediately after its review gate.

---

### Task 1: Compile and Validate the Release Lifecycle Catalog

**Files:**
- Create: `src/hal0/lifecycle/__init__.py`
- Create: `src/hal0/lifecycle/types.py`
- Create: `src/hal0/lifecycle/catalog.py`
- Create: `src/hal0/lifecycle/data/packages.toml`
- Create: `src/hal0/lifecycle/data/runners.toml`
- Create: `src/hal0/lifecycle/data/models.toml`
- Create: `src/hal0/lifecycle/data/profiles.toml`
- Create: `src/hal0/lifecycle/data/bootstrap.toml`
- Create: `scripts/compile-lifecycle-catalog.py`
- Create: `scripts/check-package-catalog.py`
- Create: `tests/lifecycle/test_catalog_models.py`
- Create: `tests/lifecycle/test_catalog_compile.py`
- Create: `tests/lifecycle/test_catalog_validate.py`
- Modify: `scripts/release-check.sh`

**Interfaces:**
- Consumes: authored TOML and authenticated GitHub/Hugging Face metadata during release validation.
- Produces: `LifecycleCatalog.load_bundled()`, `CatalogReport`, canonical `catalog.json`.

- [ ] **Step 1: Write failing shape and invariant tests**

```python
from hal0.lifecycle.catalog import CatalogError, LifecycleCatalog


def test_catalog_rejects_mutable_runner_image(catalog_source):
    catalog_source.runner("cpu")["package"] = "ghcr.io/hal0ai/cpu:latest"
    with pytest.raises(CatalogError, match="immutable digest"):
        LifecycleCatalog.from_documents(catalog_source.documents).validate()


def test_catalog_has_one_deterministic_default_per_host(catalog):
    report = catalog.validate()
    assert report.errors == ()
    assert catalog.default_runner(host="amd-vulkan", capability="chat").id


def test_rocmfpx_model_cannot_use_stock_llama(catalog):
    decision = catalog.compatibility(model="hal0-brain-rocmfpx-agent", runner="vulkan")
    assert decision.compatible is False
    assert decision.reason_code == "model_format.unsupported"
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/lifecycle/test_catalog_models.py tests/lifecycle/test_catalog_validate.py -q
```

Expected: import failure because `hal0.lifecycle` does not exist.

- [ ] **Step 3: Implement immutable Pydantic shapes**

Define frozen models in `types.py` for `PackageDefinition`, `RunnerDefinition`, `ModelDefinition`, `ModelFile`, `PromptContract`, `ProfileDefinition`, `BootstrapPolicy`, `CatalogEnvelope`, `CompatibilityResult`, and `CatalogReport`. Use constrained digest types that require `sha256:<64 lowercase hex>` for images and 64 lowercase hex for files.

```python
Sha256Image = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
Sha256File = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

class PackageDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    repository: str
    digest: Sha256Image
    package_kind: Literal["runner", "service", "toolbox", "ui", "migration"]
    platforms: tuple[str, ...]
```

- [ ] **Step 4: Implement one loader/index/validator**

`LifecycleCatalog.from_documents()` parses all authored documents, builds indexes once, and validates:

- unique IDs and package repository/digest pairs;
- complete references;
- deterministic defaults;
- format/runner compatibility;
- model revisions, filenames, sizes, checksums, and licenses;
- deprecation replacement integrity;
- initial slot policy containing only `agent` plus conditional `brain`.

Keep `validate()` pure and return all errors in stable sorted order.

- [ ] **Step 5: Populate reviewed catalog facts**

Use authenticated inventory commands:

```bash
gh api --paginate '/orgs/Hal0ai/packages?package_type=container&per_page=100' > /tmp/hal0-packages.json
python scripts/check-package-catalog.py --github-json /tmp/hal0-packages.json --catalog src/hal0/lifecycle/data/packages.toml
```

Represent every visible Hal0ai container package or an explicit reviewed exclusion. Convert existing `RUNNER_IMAGES` entries first; pin every package digest. Add the three brain candidates in this order:

1. `Hal0ai/hal0-brain-sft-fpx8-agent` with ROCmFPX-only format constraints;
2. `Hal0ai/hal0-brain-sft-GGUF` with documented file SHA-256 `ed9d28c4eac1d7c291bc80d9410c243a3d28e655921ccaf90f2b6619aa24d2c3` and an immutable HF revision;
3. `ewinregirgojr/MiniCPM5-1B-Agentic-Tooluse-GGUF` Q8_0 with immutable revision/file digest and XML tool prompt contract.

The compiler must refuse missing immutable metadata instead of emitting incomplete entries.

- [ ] **Step 6: Compile deterministically**

```bash
uv run python scripts/compile-lifecycle-catalog.py --check
uv run python scripts/compile-lifecycle-catalog.py --write
uv run python scripts/compile-lifecycle-catalog.py --check
```

Expected: first check reports absent/stale generated output; write succeeds; second check exits zero.

- [ ] **Step 7: Add release checks without CI workflow edits**

Have `scripts/release-check.sh` invoke compiler `--check` and catalog validation. Do not edit `.github/workflows/`.

- [ ] **Step 8: Verify and commit**

```bash
uv run pytest tests/lifecycle/test_catalog_models.py tests/lifecycle/test_catalog_compile.py tests/lifecycle/test_catalog_validate.py -q
uv run ruff check src/hal0/lifecycle tests/lifecycle scripts/compile-lifecycle-catalog.py scripts/check-package-catalog.py
uv run ruff format --check src/hal0/lifecycle tests/lifecycle scripts/compile-lifecycle-catalog.py scripts/check-package-catalog.py
scripts/release-check.sh --local
graphify update .
git add src/hal0/lifecycle scripts/compile-lifecycle-catalog.py scripts/check-package-catalog.py tests/lifecycle scripts/release-check.sh
git commit -m "feat: add validated lifecycle catalog"
```

---

### Task 2: Resolve Host, Runner, Model, and Bootstrap Decisions

**Files:**
- Modify: `src/hal0/lifecycle/types.py`
- Modify: `src/hal0/lifecycle/catalog.py`
- Modify: `src/hal0/runners/__init__.py`
- Modify: `src/hal0/install/profile_derive.py`
- Create: `tests/lifecycle/test_resolver.py`
- Create: `tests/lifecycle/test_brain_fallback.py`
- Modify: `tests/runners/test_registry.py`
- Modify: `tests/runners/test_resolve_image.py`

**Interfaces:**
- Consumes: `ResolutionRequest(host, intent, installed, purpose)`.
- Produces: `ResolutionPlan` with operations, decisions, rejection reasons, warnings, and download estimate.

- [ ] **Step 1: Write failing resolver tests**

```python
def test_fresh_install_plans_only_agent_and_default_runner(catalog, amd_host):
    plan = catalog.resolve(ResolutionRequest.fresh_install(host=amd_host))
    assert [op.resource.id for op in plan.operations if op.kind == "slot.ensure"] == ["agent"]
    assert plan.selection("agent.runner").selected.id == "vulkan"
    assert not [op for op in plan.operations if op.kind == "model.pull"]


def test_brain_fallback_prefers_hal0_stock_before_minicpm(catalog, stock_host, hermes_intent):
    plan = catalog.resolve(ResolutionRequest.setup(host=stock_host, intent=hermes_intent))
    decision = plan.selection("brain.model")
    assert decision.selected.id == "hal0-brain-stock"
    assert decision.rejected[0].reason_code == "runner.rocmfpx_unavailable"


def test_existing_slot_pin_is_never_changed(catalog, installed_with_custom_pin):
    plan = catalog.compare(installed_with_custom_pin)
    assert not [op for op in plan.operations if op.kind == "slot.runner.set"]
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/lifecycle/test_resolver.py tests/lifecycle/test_brain_fallback.py -q
```

- [ ] **Step 3: Add frozen request/plan/result types**

Implement `HostFacts`, `OperatorIntent`, `InstalledState`, `ResourceRef`, `RejectedCandidate`, `SelectionDecision`, `ActionRef`, `LifecycleOperation`, `ResolutionPlan`, and `UpdatePlan`. Keep plans serializable and secret-free.

- [ ] **Step 4: Implement indexed resolution**

At catalog construction, index runners by capability, architecture, device kind, backend, and format. `resolve()` filters in that order, ranks by explicit priority, and produces stable rejected-candidate reasons. It performs no I/O.

- [ ] **Step 5: Preserve the runner compatibility facade**

Change `RUNNER_IMAGES`, `get_runner()`, `runner_for_backend()`, `runner_matches()`, and `resolve_runner_image()` to read bundled catalog records while preserving existing call signatures during migration. Remove the hardcoded registry only after all direct callers use the facade.

- [ ] **Step 6: Verify performance and commit**

```bash
uv run pytest tests/lifecycle/test_resolver.py tests/lifecycle/test_brain_fallback.py tests/runners/test_registry.py tests/runners/test_resolve_image.py -q
uv run python - <<'PY'
from timeit import timeit
from hal0.lifecycle.catalog import LifecycleCatalog
catalog = LifecycleCatalog.load_bundled()
request = catalog.example_request("amd-vulkan")
assert timeit(lambda: catalog.resolve(request), number=1000) < 1.0
PY
uv run ruff check src/hal0/lifecycle src/hal0/runners tests/lifecycle tests/runners
graphify update .
git add src/hal0/lifecycle src/hal0/runners src/hal0/install/profile_derive.py tests/lifecycle tests/runners
git commit -m "feat: resolve lifecycle compatibility from catalog"
```

---

### Task 3: Make Built-In Profiles Virtual and Rescue Legacy Divergence

**Files:**
- Modify: `src/hal0/profiles/__init__.py`
- Modify: `src/hal0/config/seeds.py`
- Modify: `src/hal0/updater/updater.py`
- Modify: `tests/profiles/test_catalog.py`
- Create: `tests/profiles/test_builtin_rescue.py`
- Modify: `tests/updater/test_seed_profiles_migration.py`

**Interfaces:**
- Consumes: lifecycle catalog built-ins and materialized legacy profiles.
- Produces: virtual read-only built-ins plus collision-free custom rescue records.

- [ ] **Step 1: Write failing migration tests**

```python
def test_unchanged_materialized_builtin_is_removed_once(profile_store, catalog):
    profile_store.write_materialized("chat", catalog.profile("chat").to_profile_config())
    first = profile_store.rescue_legacy_builtins(catalog)
    second = profile_store.rescue_legacy_builtins(catalog)
    assert first.removed == ("chat",)
    assert second.changed is False


def test_divergent_builtin_becomes_custom(profile_store, catalog):
    profile_store.write_materialized("chat", ProfileConfig(flags=["--ctx-size", "8192"]))
    result = profile_store.rescue_legacy_builtins(catalog)
    rescued = profile_store.resolve(result.rescued[0])
    assert rescued.cloned_from == "chat"
    assert rescued.flags == ["--ctx-size", "8192"]
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/profiles/test_builtin_rescue.py -q
```

- [ ] **Step 3: Deepen `ProfileCatalog` rather than adding another store**

Add `builtins`, `duplicate_builtin()`, and `rescue_legacy_builtins()` to the existing profile interface. Built-ins come from `LifecycleCatalog`; custom profiles remain in `profiles.toml`. Use a stable content fingerprint and persisted migration marker so reruns never produce duplicate rescues.

- [ ] **Step 4: Replace updater seed mutation with the profile interface**

Keep `ensure_seed_profiles()` as a compatibility entry point, but delegate to `ProfileCatalog.rescue_legacy_builtins()`. Delete field-level duplicate logic after tests prove parity.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/profiles/test_catalog.py tests/profiles/test_builtin_rescue.py tests/updater/test_seed_profiles_migration.py -q
uv run ruff check src/hal0/profiles src/hal0/config/seeds.py src/hal0/updater/updater.py tests/profiles tests/updater/test_seed_profiles_migration.py
graphify update .
git add src/hal0/profiles src/hal0/config/seeds.py src/hal0/updater/updater.py tests/profiles tests/updater/test_seed_profiles_migration.py
git commit -m "feat: converge immutable built-in profiles"
```

---

### Task 4: Persist Lifecycle Jobs, Issues, Actions, and Recommendations

**Files:**
- Create: `src/hal0/db/migrations/006_lifecycle.sql`
- Create: `src/hal0/lifecycle/state.py`
- Create: `src/hal0/lifecycle/converger.py`
- Modify: `src/hal0/lifecycle/types.py`
- Create: `tests/lifecycle/test_state.py`
- Create: `tests/lifecycle/test_converger.py`
- Modify: `tests/db/test_migrate.py`

**Interfaces:**
- Consumes: `LifecyclePlan` and injected operation adapters.
- Produces: transactional `LifecycleResult`; durable idempotent jobs/issues/recommendations.

- [ ] **Step 1: Write failing migration/state tests**

```python
def test_issue_upsert_is_idempotent(lifecycle_state):
    issue = ReadinessIssue.runner_pull_failed("sha256:" + "a" * 64, "network.timeout")
    lifecycle_state.upsert_issue(issue)
    lifecycle_state.upsert_issue(issue)
    assert lifecycle_state.list_issues() == [issue]


def test_dismissal_is_scoped_to_catalog_revision(lifecycle_state):
    lifecycle_state.upsert_recommendation(recommendation("rev-1"))
    lifecycle_state.dismiss_recommendation("runner-update:agent", "rev-1")
    lifecycle_state.upsert_recommendation(recommendation("rev-2"))
    assert lifecycle_state.get_recommendation("runner-update:agent").dismissed is False
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/lifecycle/test_state.py tests/db/test_migrate.py -q
```

- [ ] **Step 3: Add normalized SQLite tables**

Migration `006_lifecycle.sql` creates `catalog_activation`, `artifact_inventory`, `lifecycle_job`, `readiness_issue`, and `recommendation`, with unique idempotency keys, catalog revision fields, terminal timestamps, and foreign-key-safe JSON payload columns only for typed action/resource envelopes.

- [ ] **Step 4: Implement transactional repository methods**

Use existing `connect()` and `tx()`; do not create a database pool or second DB. Repository methods return typed records and use `INSERT ... ON CONFLICT DO UPDATE` for issue/job convergence.

- [ ] **Step 5: Implement converger ordering and fatality**

`LifecycleConverger.apply()` checks preconditions, skips completed idempotency keys, applies operations by phase, records results, runs declared compensation for fatal failures, and persists nonfatal issues. Inject adapters; never instantiate podman/systemd/HF clients inside the converger.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/db/test_migrate.py tests/lifecycle/test_state.py tests/lifecycle/test_converger.py -q
uv run ruff check src/hal0/lifecycle src/hal0/db tests/lifecycle tests/db
graphify update .
git add src/hal0/lifecycle src/hal0/db tests/lifecycle tests/db/test_migrate.py
git commit -m "feat: persist lifecycle convergence state"
```

---

### Task 5: Normalize Host Support Behind apt, dnf, and pacman Adapters

**Files:**
- Create: `src/hal0/lifecycle/host.py`
- Modify: `src/hal0/hardware/probe.py`
- Modify: `src/hal0/hardware/pve.py`
- Modify: `installer/lib/distro.sh`
- Modify: `installer/lib/preflight.sh`
- Create: `tests/lifecycle/test_host_platform.py`
- Create: `tests/lifecycle/test_host_adapters.py`
- Modify: `tests/hardware/test_probe.py`
- Modify: `tests/hardware/test_pve.py`

**Interfaces:**
- Consumes: filesystem/process inspection and `HostRequirements`.
- Produces: normalized `HostFacts`, `HostReport`, and batched `HostPlan` results.

- [ ] **Step 1: Write adapter contract tests**

```python
@pytest.mark.parametrize(
    ("os_release", "adapter"),
    [("ID=ubuntu", "apt"), ("ID=rocky", "dnf"), ("ID=arch", "pacman")],
)
def test_supported_distro_selects_adapter(fake_host, os_release, adapter):
    fake_host.write("/etc/os-release", os_release)
    assert HostPlatform.inspect(fake_host).package_adapter == adapter


def test_wsl1_refuses_before_mutation(fake_host, recording_runner):
    fake_host.kernel_release = "4.4.0-Microsoft"
    report = HostPlatform(recording_runner, fake_host).preflight(BASE_REQUIREMENTS)
    assert report.supported is False
    assert report.reason_code == "host.wsl1_unsupported"
    assert recording_runner.calls == []
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/lifecycle/test_host_platform.py tests/lifecycle/test_host_adapters.py -q
```

- [ ] **Step 3: Implement capability-oriented host facts**

Distinguish bare metal, VM, privileged LXC, unprivileged LXC, WSL1, and WSL2. Detect init, package manager, user namespaces, cgroups, Podman, systemd, mount traits, render nodes, and device passthrough. Keep raw evidence for diagnostics.

- [ ] **Step 4: Implement batched adapters**

Each package adapter translates `ensure_packages((...))` into one transaction. The systemd adapter supports install, daemon-reload, enable, start, stop, disable, and no-start without owning lifecycle policy.

- [ ] **Step 5: Make shell a thin adapter caller**

During transition, shell invokes Python host inspection as JSON and retains only bootstrap-safe checks required before Python is available. Delete duplicated shell decisions only after parity tests pass.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/lifecycle/test_host_platform.py tests/lifecycle/test_host_adapters.py tests/hardware/test_probe.py tests/hardware/test_pve.py -q
uv run ruff check src/hal0/lifecycle/host.py src/hal0/hardware tests/lifecycle tests/hardware
bash -n installer/lib/distro.sh installer/lib/preflight.sh
graphify update .
git add src/hal0/lifecycle/host.py src/hal0/hardware installer/lib/distro.sh installer/lib/preflight.sh tests/lifecycle tests/hardware
git commit -m "feat: normalize supported host platforms"
```

---

### Task 6: Collect Essential Install Configuration Before Mutation

**Files:**
- Create: `src/hal0/install/essentials.py`
- Modify: `src/hal0/install/answers.py`
- Modify: `src/hal0/cli/setup_plan.py`
- Modify: `installer/install.sh`
- Create: `tests/install/test_essentials.py`
- Modify: `tests/install/test_answers.py`
- Modify: `tests/install/test_setup_plan.py`

**Interfaces:**
- Consumes: raw interactive values, answer-file references, and `HostFacts`.
- Produces: validated secret-free `OperatorIntent` and a redacted confirmation summary.

- [ ] **Step 1: Write failing validation tests**

```python
def test_public_bind_requires_auth(tmp_path, host_facts):
    raw = EssentialAnswers(model_store=tmp_path, bind_host="0.0.0.0", auth_mode="disabled")
    with pytest.raises(InstallValidationError, match="authentication"):
        validate_essentials(raw, host_facts)


def test_validation_has_no_filesystem_side_effects(tmp_path, host_facts):
    target = tmp_path / "models"
    intent = validate_essentials(valid_answers(target), host_facts)
    assert intent.model_store == target
    assert target.exists() is False
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/install/test_essentials.py -q
```

- [ ] **Step 3: Implement typed collection and validation**

Fields: model store, configurable state/config roots, bind host, advertised hostname, API port, allowed origins, auth mode, generated/provided credential reference, service UID/GID behavior, and ownership consent. Validate free space, writable ancestor, mount traits, UID collision, port conflict, origin syntax, and public-bind auth.

- [ ] **Step 4: Wire install shell to Python collection**

`installer/install.sh` obtains raw answers, invokes `hal0 install essentials --plan-json`, prints the redacted summary, confirms, and only then creates users/directories. Add `--answers`, `--non-interactive`, and `--no-setup` parity without embedding secrets in argv.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/install/test_essentials.py tests/install/test_answers.py tests/install/test_setup_plan.py -q
bash -n installer/install.sh
uv run ruff check src/hal0/install tests/install
graphify update .
git add src/hal0/install installer/install.sh tests/install
git commit -m "feat: validate install essentials before mutation"
```

---

### Task 7: Converge a Minimal Base Install and Pull the Default Runner

**Files:**
- Modify: `src/hal0/lifecycle/converger.py`
- Modify: `src/hal0/install/orchestrate.py`
- Modify: `src/hal0/install/static_seeds.py`
- Modify: `installer/install.sh`
- Delete: untouched capability seed TOMLs from `installer/etc-hal0/slots/`
- Create: `tests/install/test_minimal_base.py`
- Modify: `tests/install/test_orchestrate.py`
- Modify: `tests/install/test_static_seeds.py`

**Interfaces:**
- Consumes: fresh-install `ResolutionPlan`.
- Produces: base release, virtual profiles, default runner job, and one `agent` slot.

- [ ] **Step 1: Write failing minimal-install tests**

```python
async def test_base_install_seeds_only_enabled_empty_agent(converger, fresh_plan, slot_store):
    result = await converger.apply(fresh_plan)
    slots = list(slot_store.iter_configs())
    assert result.status in {"succeeded", "degraded"}
    assert [slot.name for slot in slots] == ["agent"]
    assert slots[0].enabled is True
    assert slots[0].model is None
    assert slots[0].runner_digest.startswith("sha256:")


def test_base_install_has_no_capability_scaffolds(slot_store):
    assert not {"tts", "stt", "embed", "rerank", "img", "utility"} & slot_store.names()
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/install/test_minimal_base.py tests/install/test_static_seeds.py -q
```

- [ ] **Step 3: Replace static roster ownership**

Make `seed_static_slots()` delegate to bootstrap policy and create only agent. Remove Bash copy loops and untouched capability seed files. Migration must preserve configured/stateful existing slots and remove only untouched empty legacy scaffolds.

- [ ] **Step 4: Represent enabled-empty correctly**

Add readiness derivation so enabled + missing model becomes `waiting_for_model`; do not invoke container start or create a restart loop. Routing returns typed unavailable guidance until model assignment.

- [ ] **Step 5: Pull the selected default runner nonfatally**

Use existing `run_image_pull()` through an adapter. Success verifies digest and artifact inventory. Network/pull failure records a job plus readiness issue and returns degraded install status; it does not roll back the healthy control plane.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/install/test_minimal_base.py tests/install/test_orchestrate.py tests/install/test_static_seeds.py tests/slots/test_manager_readiness_api.py -q
bash -n installer/install.sh
uv run ruff check src/hal0/install src/hal0/lifecycle tests/install
graphify update .
git add src/hal0/install src/hal0/lifecycle installer/install.sh installer/etc-hal0/slots tests/install
git commit -m "feat: converge minimal base installation"
```

---

### Task 8: Expose Durable Readiness and Artifact Retry Actions

**Files:**
- Create: `src/hal0/api/routes/lifecycle.py`
- Create: `src/hal0/cli/lifecycle_commands.py`
- Modify: `src/hal0/api/__init__.py`
- Modify: `src/hal0/cli/main.py`
- Modify: `ui/src/api/endpoints.ts`
- Modify: `ui/src/dash/dashboard-redesign.jsx`
- Create: `ui/src/dash/lifecycle-alerts.jsx`
- Create: `tests/api/test_lifecycle_routes.py`
- Create: `tests/cli/test_lifecycle_commands.py`
- Create: `ui/tests/unit/lifecycle-alerts.test.jsx`

**Interfaces:**
- Produces: readiness list, job status, retry/cancel actions, and authenticated progress stream from shared typed records.

- [ ] **Step 1: Write failing API/CLI tests**

```python
def test_retry_returns_same_job_for_same_issue(client, lifecycle_state):
    issue = lifecycle_state.add_runner_failure()
    first = client.post(f"/api/lifecycle/issues/{issue.id}/retry").json()
    second = client.post(f"/api/lifecycle/issues/{issue.id}/retry").json()
    assert first["job_id"] == second["job_id"]


def test_readiness_response_is_redacted(client, lifecycle_state):
    lifecycle_state.add_failure(detail="authorization: Bearer secret-value")
    body = client.get("/api/lifecycle/readiness").text
    assert "secret-value" not in body
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/api/test_lifecycle_routes.py tests/cli/test_lifecycle_commands.py -q
```

- [ ] **Step 3: Add one lifecycle route module and one CLI group**

Routes: `GET /api/lifecycle/readiness`, `GET /api/lifecycle/jobs/{id}`, `POST /api/lifecycle/issues/{id}/retry`, `POST /api/lifecycle/jobs/{id}/cancel`, and authenticated progress stream. CLI: `hal0 lifecycle readiness`, `hal0 lifecycle retry <issue-id>`, and `hal0 lifecycle jobs <id>` with human/JSON output.

- [ ] **Step 4: Render shared WebUI alerts**

One bounded readiness query renders global alerts and resource links. Warning treatment uses orange border plus icon/text. Retry invokes the typed action and displays job progress; no UI-only issue store.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/api/test_lifecycle_routes.py tests/cli/test_lifecycle_commands.py -q
cd ui && npm run test:unit -- lifecycle-alerts && npm run lint && npm run build
cd ..
uv run ruff check src/hal0/api/routes/lifecycle.py src/hal0/cli/lifecycle_commands.py tests/api/test_lifecycle_routes.py tests/cli/test_lifecycle_commands.py
graphify update .
git add src/hal0/api src/hal0/cli ui/src ui/tests tests/api/test_lifecycle_routes.py tests/cli/test_lifecycle_commands.py
git commit -m "feat: expose lifecycle readiness and retries"
```

---

### Task 9: Provision Hermes and Brain Through the Catalog Fallback Chain

**Files:**
- Create: `src/hal0/install/brain_setup.py`
- Modify: `src/hal0/install/extensions.py`
- Modify: `src/hal0/install/orchestrate.py`
- Modify: `src/hal0/agents/hermes_provision.py`
- Modify: `src/hal0/cli/setup_command.py`
- Create: `tests/install/test_brain_setup.py`
- Modify: `tests/install/test_orchestrate.py`
- Modify: `tests/agents/test_hermes_provision.py`

**Interfaces:**
- Consumes: Hermes opt-out/presence/health and resolver brain decision.
- Produces: absent brain, or one catalog-configured brain slot with verified model/artifact state.

- [ ] **Step 1: Write failing conditional-provision tests**

```python
async def test_brain_is_absent_when_hermes_opted_out(setup, slot_store):
    await setup.run(OperatorIntent(install_hermes=False))
    assert "brain" not in slot_store.names()


async def test_existing_healthy_hermes_seeds_brain(setup, fake_hermes, slot_store):
    fake_hermes.present = True
    fake_hermes.healthy = True
    result = await setup.run(OperatorIntent(install_hermes=True))
    assert slot_store.get("brain").profile == "hal0-brain"
    assert result.selection("brain.model").selected.id == "hal0-brain-stock"


async def test_brain_pull_failure_leaves_disabled_actionable_slot(setup, failing_pull, state):
    result = await setup.run(OperatorIntent(install_hermes=True))
    assert result.slot("brain").enabled is False
    assert state.list_issues(resource="slot:brain")[0].retry_action is not None
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/install/test_brain_setup.py -q
```

- [ ] **Step 3: Extract a narrow Hermes adapter**

Reuse existing Hermes provision implementation through `detect()`, `install()`, and `health()` adapter methods. Do not duplicate its 5,000-line pipeline. Existing healthy Hermes auto-integrates; absent Hermes is enabled by default with explicit opt-out.

- [ ] **Step 4: Apply the three-level brain policy**

Resolve ROCmFPX, hal0 stock F16, then MiniCPM5 Q8_0. Persist selected model/runner/profile and every rejection reason. Create brain only after Hermes health passes. Verify model checksum before assignment.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/install/test_brain_setup.py tests/install/test_orchestrate.py tests/agents/test_hermes_provision.py -q
uv run ruff check src/hal0/install src/hal0/agents/hermes_provision.py tests/install tests/agents/test_hermes_provision.py
graphify update .
git add src/hal0/install src/hal0/agents/hermes_provision.py src/hal0/cli/setup_command.py tests/install tests/agents/test_hermes_provision.py
git commit -m "feat: converge conditional Hermes brain setup"
```

---

### Task 10: Integrate Catalog Comparison, Runner Retention, and Recommendations into Updater

**Files:**
- Modify: `src/hal0/updater/updater.py`
- Modify: `src/hal0/lifecycle/catalog.py`
- Modify: `src/hal0/lifecycle/converger.py`
- Modify: `src/hal0/lifecycle/state.py`
- Create: `tests/updater/test_lifecycle_catalog_update.py`
- Create: `tests/updater/test_runner_retention.py`
- Create: `tests/updater/test_runner_recommendations.py`
- Modify: `tests/updater/test_updater.py`

**Interfaces:**
- Consumes: staged release/catalog and installed state.
- Produces: `UpdatePlan`, retained rollback artifacts, explicit per-slot recommendations, atomic catalog activation.

- [ ] **Step 1: Write failing update tests**

```python
def test_update_prepulls_new_default_without_repinning_slot(updater, installed_slot):
    updater.prepare()
    updater.commit()
    assert updater.images.pulled == ["sha256:new-default"]
    assert installed_slot.runner_digest == "sha256:old-default"
    assert updater.state.recommendation_for(installed_slot.id).available.id == "new-default"


def test_rollback_restores_catalog_and_retains_both_images(updater):
    updater.apply()
    updater.rollback()
    assert updater.catalog.active_revision == "old-revision"
    assert updater.images.present == {"sha256:old-default", "sha256:new-default"}
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/updater/test_lifecycle_catalog_update.py tests/updater/test_runner_retention.py tests/updater/test_runner_recommendations.py -q
```

- [ ] **Step 3: Add comparison to prepare/commit without a second updater**

`prepare()` loads and validates staged catalog, snapshots installed state, and creates `UpdatePlan`. `commit()` attempts default pre-pull, runs profile/secret migrations, atomically activates release+catalog, restarts required services, and stores recommendations. Existing slot pins remain unchanged.

- [ ] **Step 4: Add retention/cleanup safety**

Retain prior release-owned default until rollback deadline. Cleanup requires deadline expiry plus no slot/profile/job/staged-release reference and known release ownership. Never remove unknown/operator artifacts.

- [ ] **Step 5: Verify rollback failure boundaries and commit**

```bash
uv run pytest tests/updater/test_lifecycle_catalog_update.py tests/updater/test_runner_retention.py tests/updater/test_runner_recommendations.py tests/updater/test_updater.py tests/updater/test_seed_profiles_migration.py -q
uv run ruff check src/hal0/updater src/hal0/lifecycle tests/updater
graphify update .
git add src/hal0/updater src/hal0/lifecycle tests/updater
git commit -m "feat: reconcile lifecycle catalog during updates"
```

---

### Task 11: Repair Slot Enable/Disable as One Domain Operation

**Files:**
- Create: `src/hal0/slots/enablement.py`
- Modify: `src/hal0/slots/interface.py`
- Modify: `src/hal0/slots/manager.py`
- Modify: `src/hal0/dispatcher/router.py`
- Modify: `src/hal0/api/routes/slots.py`
- Modify: `src/hal0/cli/slot_commands.py`
- Modify: `src/hal0/capabilities/orchestrator.py`
- Create: `tests/slots/test_enablement.py`
- Modify: `tests/slots/test_interface.py`
- Modify: `tests/dispatcher/test_router.py`
- Create: `tests/api/test_slot_enablement.py`

**Interfaces:**
- Produces: `set_enabled(slot_id, enabled) -> EnablementResult` shared by API, CLI, capability toggles, reconciliation, and tests.

- [ ] **Step 1: Write failing contract tests**

```python
async def test_disabled_slot_is_not_started_routed_or_woken(enablement, dispatcher, systemd):
    await enablement.set_enabled("agent", False)
    assert systemd.is_enabled("hal0-slot@agent.service") is False
    assert await dispatcher.resolve_for_request("hal0/agent") is None
    assert systemd.start_calls == []


async def test_reboot_reconciliation_preserves_disabled_intent(enablement, systemd):
    systemd.enable("hal0-slot@agent.service")
    await enablement.reconcile()
    assert systemd.is_enabled("hal0-slot@agent.service") is False
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/slots/test_enablement.py tests/api/test_slot_enablement.py -q
```

- [ ] **Step 3: Implement atomic intent-first enablement**

Persist config intent, converge systemd unit enable/start state, update runtime state/routing, and return typed readiness. On partial failure, preserve explicit intent and create an actionable readiness issue; never infer intent from container state.

- [ ] **Step 4: Route every caller through the new interface**

Replace direct `enabled` mutations in API, CLI, capability orchestration, and startup reconciliation. Add config-enabled guard before dispatcher wake/load logic.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/slots/test_enablement.py tests/slots/test_interface.py tests/api/test_slot_enablement.py tests/dispatcher/test_router.py tests/slots/test_npu_exclusivity.py -q
uv run ruff check src/hal0/slots src/hal0/dispatcher src/hal0/capabilities tests/slots tests/dispatcher tests/api/test_slot_enablement.py
graphify update .
git add src/hal0/slots src/hal0/dispatcher src/hal0/api/routes/slots.py src/hal0/cli/slot_commands.py src/hal0/capabilities tests/slots tests/dispatcher tests/api/test_slot_enablement.py
git commit -m "fix: make slot enablement authoritative"
```

---

### Task 12: Add Device-First Compatible Runner Selection and Attention UX

**Files:**
- Modify: `src/hal0/api/routes/lifecycle.py`
- Modify: `src/hal0/api/routes/slots.py`
- Modify: `ui/src/api/endpoints.ts`
- Create: `ui/src/dash/slots/RunnerPicker.jsx`
- Modify: `ui/src/dash/slots/CreateSlotModal.jsx`
- Modify: `ui/src/dash/slot-modals.jsx`
- Modify: `ui/src/dash/dashboard-redesign.jsx`
- Create: `tests/api/test_compatible_runners.py`
- Create: `ui/tests/unit/runner-picker.test.jsx`
- Create: `ui/tests/e2e/runner-recommendations.spec.ts`

**Interfaces:**
- Produces: resolver-backed compatible runner rows and explicit recommendation upgrade/dismiss actions.

- [ ] **Step 1: Write failing API contract test**

```python
def test_compatible_runners_explain_current_and_available(client, slot_with_old_runner):
    rows = client.get(f"/api/slots/{slot_with_old_runner.id}/compatible-runners").json()
    recommended = next(row for row in rows if row["recommended"])
    assert recommended["state"] in {"installed", "available", "pulling"}
    assert recommended["digest"].startswith("sha256:")
    assert recommended["reason_code"]
    assert rows[0]["model_formats"]
```

- [ ] **Step 2: Write failing UI test**

```jsx
it("keeps the current runner selected and marks the new compatible runner", async () => {
  render(<RunnerPicker value="old" device="amd-gpu" slotId="agent" />)
  expect(await screen.findByText("New compatible runner available")).toBeVisible()
  expect(screen.getByRole("combobox")).toHaveValue("old")
  expect(screen.getByText("Recommended · download required")).toBeVisible()
})
```

- [ ] **Step 3: Verify red**

```bash
uv run pytest tests/api/test_compatible_runners.py -q
cd ui && npm run test:unit -- runner-picker
```

- [ ] **Step 4: Implement server-side compatibility rows**

Select device/backend first. API returns display name, runtime family, recommended/current flags, artifact state, capabilities, formats, current/available digests, size, deprecation, compatibility, and reason. Client does not reproduce compatibility logic.

- [ ] **Step 5: Implement accessible attention and explicit upgrade**

Show orange border plus icon/text, exact current versus available version, download status, and confirmation. Upgrade action validates the assigned model again. Dismissal is scoped to exact catalog revision/digest.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/api/test_compatible_runners.py -q
cd ui
npm run test:unit -- runner-picker
npm run lint
npm run build
npx playwright test tests/e2e/runner-recommendations.spec.ts --reporter=line
cd ..
graphify update .
git add src/hal0/api ui/src ui/tests tests/api/test_compatible_runners.py
git commit -m "feat: guide compatible runner selection"
```

---

### Task 13: Isolate and Migrate API, Hugging Face, and Proxmox Secrets

**Files:**
- Create: `src/hal0/secrets/store.py`
- Modify: `src/hal0/api/_env_store.py`
- Modify: `src/hal0/api/routes/secrets.py`
- Modify: `src/hal0/hardware/pve.py`
- Modify: `src/hal0/install/answers.py`
- Modify: `src/hal0/install/orchestrate.py`
- Modify: `src/hal0/cli/setup_command.py`
- Modify: `installer/install.sh`
- Modify: `installer/uninstall.sh`
- Create: `tests/secrets/test_store.py`
- Create: `tests/secrets/test_migration.py`
- Modify: `tests/api/test_secrets.py`
- Modify: `tests/hardware/test_pve.py`
- Modify: `tests/install/test_answers.py`

**Interfaces:**
- Produces: `SecretStore.status/write/delete/migrate_legacy` with integration IDs `api-auth`, `huggingface`, and `proxmox`.

- [ ] **Step 1: Write failing security tests**

```python
def test_each_integration_uses_root_owned_0600_file(secret_store):
    secret_store.write("huggingface", {"HF_TOKEN": "secret"})
    path = secret_store.path_for("huggingface")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.stat().st_uid == 0


def test_migration_is_atomic_redacted_and_idempotent(secret_store, legacy_env):
    first = secret_store.migrate_legacy(legacy_env)
    second = secret_store.migrate_legacy(legacy_env)
    assert first.migrated == ("api-auth", "huggingface")
    assert second.changed is False
    assert "secret-value" not in repr(first)
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/secrets/test_store.py tests/secrets/test_migration.py -q
```

- [ ] **Step 3: Implement one secret store with per-integration files**

Paths:

```text
/etc/hal0/secrets/api-auth.env
/etc/hal0/secrets/huggingface.env
/etc/hal0/secrets/proxmox.env
```

Use atomic temp/write/fsync/rename, root ownership, `0600`, integration key allowlists, control-character rejection, and masked status. Services receive only required files through systemd environment/credential seams.

- [ ] **Step 4: Wire setup fields and answer references**

Setup essential stage handles API auth. Advanced stage handles HF token and Proxmox endpoint/realm/user/token ID/token secret when LXC/PVE is detected or explicitly enabled. Answer files use environment-variable names or precreated secret paths and never export values.

- [ ] **Step 5: Migrate legacy locations once**

Read `/etc/hal0/api.env`, `/var/lib/hal0/secrets/hal0-api.env`, and existing Proxmox config. Write new files first, update references, verify service access, then remove only migrated secret keys from legacy files. Keep compatibility readers for one release window.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/secrets tests/api/test_secrets.py tests/hardware/test_pve.py tests/install/test_answers.py -q
bash -n installer/install.sh installer/uninstall.sh
uv run ruff check src/hal0/secrets src/hal0/api/_env_store.py src/hal0/api/routes/secrets.py src/hal0/hardware/pve.py src/hal0/install tests/secrets
graphify update .
git add src/hal0/secrets src/hal0/api src/hal0/hardware/pve.py src/hal0/install src/hal0/cli/setup_command.py installer tests/secrets tests/api/test_secrets.py tests/hardware/test_pve.py tests/install/test_answers.py
git commit -m "feat: isolate lifecycle integration secrets"
```

---

### Task 14: Converge Conservative Uninstall, Purge, and Reinstall

**Files:**
- Modify: `src/hal0/lifecycle/catalog.py`
- Modify: `src/hal0/lifecycle/converger.py`
- Modify: `installer/uninstall.sh`
- Modify: `src/hal0/cli/main.py`
- Create: `tests/lifecycle/test_uninstall_plan.py`
- Create: `tests/installer/test_uninstall_contract.py`
- Modify: `tests/cli/test_uninstall.py`
- Create: `scripts/release-lifecycle-test.sh`

**Interfaces:**
- Consumes: uninstall purpose, purge intent, installed ownership/provenance.
- Produces: dry-run plan, conservative/purge result, preserved-state manifest, reinstall convergence.

- [ ] **Step 1: Write failing keep/remove contract tests**

```python
def test_conservative_uninstall_preserves_operator_state(uninstall_plan):
    assert uninstall_plan.action("/etc/hal0") == "preserve"
    assert uninstall_plan.action("/var/lib/hal0/models") == "preserve"
    assert uninstall_plan.action("/etc/hal0/secrets") == "preserve"
    assert uninstall_plan.action("/usr/lib/hal0/current") == "remove"


def test_purge_never_deletes_unknown_image(purge_plan):
    assert purge_plan.artifact_action("operator/custom:latest") == "preserve"
    assert purge_plan.artifact_action("sha256:release-owned") == "remove"
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/lifecycle/test_uninstall_plan.py tests/installer/test_uninstall_contract.py -q
```

- [ ] **Step 3: Generate uninstall plans from ownership/provenance**

Conservative mode removes code, units, generated runtime artifacts, and managed venvs; preserves config, slots, custom profiles, models, lifecycle records, secrets, and verified artifacts. Purge removes declared hal0-owned state after confirmation but protects unknown/operator-owned artifacts.

- [ ] **Step 4: Keep shell as privileged executor**

`installer/uninstall.sh` consumes a redacted plan, executes privileged operations, daemon-reloads, and reports residuals. Restore foreign Hermes backup safely when provenance proves hal0 moved it. Reinstall reads preserved state through the same resolver/converger and does not recreate capability scaffolds.

- [ ] **Step 5: Add the production lifecycle runner without wiring CI**

`scripts/release-lifecycle-test.sh` records fresh install, no-start, model-store permissions, default runner, empty agent, Hermes paths, degraded retry, update, rollback, reboot, conservative reinstall, purge reinstall, and ghost-unit results. It writes timestamped evidence and fails required skipped/deferred rows.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/lifecycle/test_uninstall_plan.py tests/installer/test_uninstall_contract.py tests/cli/test_uninstall.py -q
bash -n installer/uninstall.sh scripts/release-lifecycle-test.sh
uv run ruff check src/hal0/lifecycle src/hal0/cli/main.py tests/lifecycle tests/installer tests/cli/test_uninstall.py
graphify update .
git add src/hal0/lifecycle src/hal0/cli/main.py installer/uninstall.sh scripts/release-lifecycle-test.sh tests/lifecycle tests/installer tests/cli/test_uninstall.py
git commit -m "feat: converge uninstall and reinstall lifecycle"
```

---

### Task 15: Reconcile and Consolidate CI After PR #1330 Repair Lands

**Files:**
- Read: `docs/rework/ci-pr1330-repair-coordination-note.md`
- Modify only after reconciliation: final workflows named by `/ci-pr1330-repair/` handoff
- Modify: targeted obsolete tests identified after production owners are removed
- Modify: `.superpowers/sdd/progress.md`

**Interfaces:**
- Consumes: final CI repair commit, required-job inventory, durations, and replacement coverage.
- Produces: bounded required contract/integration checks, targeted capability jobs, release-only live matrix.

- [ ] **Step 1: Enforce the ownership gate**

Do not continue until the CI repair handoff provides its final commit, workflows/job names, test replacements, required/optional split, duration, remaining flakes, and verification results.

- [ ] **Step 2: Rebase/reconcile and rerun repaired checks unchanged**

Set the exact commit supplied in the CI repair handoff, verify it exists, and reconcile only after operator review:

```bash
: "${CI_REPAIR_HEAD:?export the exact final CI repair commit from its handoff}"
git cat-file -e "${CI_REPAIR_HEAD}^{commit}"
git rebase "$CI_REPAIR_HEAD"
```

Run exactly the final verification commands supplied by that session. Expected: all repaired checks pass before lifecycle CI changes.

- [ ] **Step 3: Inventory distinct failure domains**

Map tests into:

1. fast catalog/resolver/converger/host-adapter contracts;
2. one representative apt, dnf, pacman, and unprivileged-LXC integration matrix;
3. targeted/scheduled GPU, NPU, ROCmFPX, and WSL2 jobs;
4. release-only halo150/halo143 lifecycle evidence.

Reject a new required job unless it proves a distinct failure domain unavailable in an existing job.

- [ ] **Step 4: Delete only superseded shallow tests**

Remove Bash constant/static-roster/client-side-compatibility tests only when their production owner is gone and a deep interface test proves the same behavior. Keep adapter-specific parsing, command, and failure translation coverage.

- [ ] **Step 5: Wire the smallest final CI surface**

Use the repaired workflow structure rather than creating parallel workflows. Cache dependencies/build outputs, not trust-verification results. Add path filters for expensive capability jobs and keep release/live validation out of per-commit CI.

- [ ] **Step 6: Verify final gates**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
PYTHONPATH="$PWD/src" uv run python -c "from hal0.api import create_app; print(len(create_app().routes))"
uv run python scripts/check_sunset.py
uv run pytest tests/lifecycle tests/install tests/updater tests/profiles tests/secrets -q -m "not podman and not systemd and not network"
cd ui
npm run lint
npm run typecheck
npm run build
npm run test:unit
cd ..
scripts/check-bootstrap-parity.sh
scripts/release-check.sh --local
```

Then run the repaired required GitHub checks and release lifecycle matrix on halo150/halo143 plus supported WSL2 smoke. Record exact evidence under `docs/rework/deploy-validation/`.

- [ ] **Step 7: Final whole-branch review and commit**

Run an independent standards/spec/security/migration review of `66147eaa..HEAD`. Fix every Critical/Important finding and rerun its covering gate.

```bash
graphify update .
git add .github/workflows tests docs/rework/deploy-validation
git commit -m "ci: consolidate lifecycle release gates"
```

## Plan Self-Review Checklist

- Every approved design section maps to at least one task.
- No task creates a second updater, profile store, slot store, model registry, or secret value in ordinary config.
- Default agent runner is pulled; the agent has no model and no capability scaffolds are created.
- Brain exists only with healthy Hermes and follows ROCmFPX → hal0 stock F16 → MiniCPM Q8_0.
- Existing runner pins remain unchanged; recommendations are explicit and revision-scoped.
- apt/dnf/pacman and bare-metal/VM/LXC/WSL2 behavior sits behind normalized host facts/adapters.
- Optional download failures are durable and retryable; base trust/catalog/permissions failures are fatal.
- CI changes are blocked until `/ci-pr1330-repair/` integration.
- Each task has a failing test, targeted verification, graph update, commit, and independent review checkpoint.
