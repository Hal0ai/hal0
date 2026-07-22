# Lean Install, Setup, and Update Lifecycle Design

> **Date:** 2026-07-22
>
> **Status:** Approved design, pending written-spec review
>
> **Integration baseline:** `b91567fcff9c771b89808eadc71379174a0b259f`
>
> **Release endpoint:** validated v1.0.0 RC; no tag or publication without operator approval

## 1. Purpose

Make hal0 installation, setup, and update reliable and deeply integrated without adding another orchestration layer or duplicating policy across Bash, Python, API, CLI, and WebUI.

The lifecycle must produce a clean base appliance:

- install the verified hal0 release and control plane;
- collect storage, networking, authentication, and service-permission essentials before state is created;
- install the release's immutable built-in profiles;
- resolve and pull only the default compatible runner image;
- seed one enabled but model-empty `agent` slot;
- install Hermes by default with explicit opt-out;
- seed and provision `brain` only when Hermes is installed successfully;
- download no other models and create no capability-slot scaffolding;
- let operators add TTS, STT, embedding, reranking, image, and other slots later;
- preserve operator-owned slots, profiles, runner pins, and artifacts through updates;
- turn incomplete downloads into durable, actionable readiness work rather than failed or half-configured installs.

## 2. Design principles

1. **One fact, one owner.** Image compatibility, profile defaults, model fallback, and bootstrap policy each have one declarative owner.
2. **Deep module, small interface.** Callers request resolution, comparison, or convergence; they do not reimplement lifecycle decisions.
3. **Facts in catalogs, behavior in code.** Catalogs describe artifacts and constraints. The resolver implements selection and fallback algorithms.
4. **Plans before effects.** Install, setup, update, retry, and migration use the same typed plan and result shapes.
5. **Convergent and idempotent.** Repeating an operation reaches the same state without duplicating slots, profiles, downloads, or notices.
6. **Operator intent is durable.** Updates never switch an existing slot's runner pin or enable/disable state implicitly.
7. **Built-ins are release-owned; custom objects are operator-owned.** Built-ins update with the release. Custom profiles and slots are never overwritten.
8. **Digest-pinned releases.** Runtime decisions never depend on mutable container tags or unpinned model revisions.
9. **Offline-capable control plane.** Network artifact failures do not invalidate an otherwise healthy base installation.
10. **Actionable degradation.** Every nonfatal failure includes a stable reason code, affected resource, retry action, CLI command, and WebUI destination.
11. **Secrets are referenced, never copied into ordinary configuration.** Logs, plans, responses, and reports contain presence/status only.
12. **No runtime package discovery.** GitHub Packages and Hugging Face are distribution systems, not live configuration authorities.
13. **No initial feature sprawl.** Optional capabilities are added through the normal slot/capability interface after installation.
14. **Rollback remains real.** New release artifacts are staged before activation; prior verified runner and catalog state remain available through the rollback window.
15. **Test depth, not matrix size.** CI proves normalized contracts at deep seams and uses a small representative platform matrix; it does not multiply every behavior across every distro and environment.

## 3. Scope

### 3.1 Included

- Production bootstrap and installer responsibility split.
- Essential installer questions and validated answer-file parity.
- Guided, rerunnable post-install setup.
- A shipped package/runner/model/profile/bootstrap catalog.
- Default-runner installation and update behavior.
- Built-in profile refresh and legacy divergent-profile rescue.
- Minimal `agent` seeding.
- Conditional Hermes/brain provisioning and model fallback.
- Durable artifact operations, readiness issues, recommendations, and retries.
- Slot create/edit compatibility filtering and new-runner attention UX.
- Secret storage for hal0 auth, Hugging Face, and Proxmox.
- Existing-install migration.
- Slot enable/disable correctness required by capability opt-in.
- Installer, updater, API, CLI, WebUI, rollback, uninstall, and live validation contracts.

### 3.2 Excluded

- A second updater or standalone `setup.sh`, `updater.sh`, or `migration.sh`.
- Runtime scraping of all GitHub or Hugging Face packages.
- Automatic runner changes on existing slots.
- Automatic creation of TTS, STT, embedding, reranking, image, vision, or utility slots.
- General model recommendations or downloads during base installation.
- A generic workflow engine.
- A second profile, model, slot, or secret store.
- Broad unrelated refactoring of installer, updater, API, or UI modules.

### 3.3 Platform support contract

This design defines the hal0 platform-wide host contract, not only installer portability. Host classification feeds installation, update, uninstall, system integration, hardware probing, runner resolution, diagnostics, API/UI support status, and release validation.

**Tier 1:**

- Debian and Ubuntu families through an `apt` adapter;
- Fedora, RHEL, Rocky Linux, AlmaLinux, and compatible families through a `dnf` adapter;
- Arch Linux and compatible families through a `pacman` adapter;
- bare metal;
- conventional virtual machines;
- Proxmox LXC, with privileged and unprivileged containers treated as distinct environments.

**Tier 2:**

- WSL2 only when systemd and the supported container runtime are enabled;
- explicit limitations for device passthrough, host networking, service boot, and unsupported kernel features;
- the same config/catalog shapes and lifecycle interface as Tier 1, with environment-specific capabilities removed by probe results.

**Unsupported:**

- WSL1;
- WSL2 without systemd or the required container runtime;
- unknown package-manager/system-manager combinations;
- hosts missing required kernel/container primitives.

Unsupported environments fail before mutation with detected facts and actionable remediation. Tier support never implies accelerator support: ROCm, Vulkan, CUDA, NPU, render nodes, and device passthrough remain independently probed capabilities.

## 4. Ownership and module architecture

### 4.1 Shell layer

`installer/bootstrap.sh` owns only:

- channel-manifest retrieval;
- release digest and signature verification;
- safe release extraction;
- transfer of verified-release context to `installer/install.sh`.

`installer/install.sh` owns only:

- supported-OS and required-command checks;
- collection of essential values before filesystem mutation;
- service user/group and directory preparation;
- versioned release tree, shared venv, systemd units, and target installation;
- invocation of typed Python lifecycle commands;
- presentation of final base-health and next-action summaries.

Shell does not own slot rosters, profile defaults, runner lists, model choices, compatibility matrices, or migrations.

### 4.2 Lifecycle catalog module

The lifecycle catalog is one logical catalog with physically separate authored documents by concern:

```text
package inventory
runner definitions
model definitions
built-in profile definitions
bootstrap policy
```

Release packaging validates and compiles these documents into one canonical bundled representation. Runtime consumers never parse multiple independent policy sources.

### 4.3 Lifecycle resolver module

The resolver is a deep module. Its external interface is deliberately small:

```python
class LifecycleCatalog:
    def resolve(self, request: ResolutionRequest) -> ResolutionPlan: ...
    def compare(self, state: InstalledState) -> UpdatePlan: ...
    def validate(self) -> CatalogReport: ...
```

- `resolve` handles fresh install, post-install setup, slot runner choices, and artifact retries.
- `compare` handles release updates, recommendations, migrations, and rollback retention.
- `validate` enforces schema and cross-reference invariants.

Callers and tests cross this interface. Hardware probes, container clients, Hugging Face clients, secret writers, and state persistence are internal seams with production and in-memory adapters.

### 4.4 Lifecycle convergence module

One converger applies a plan:

```python
class LifecycleConverger:
    def apply(self, plan: LifecyclePlan) -> LifecycleResult: ...
```

It owns ordering, transactions, idempotency keys, compensating actions, artifact jobs, readiness issues, and audit results. Installer, setup, updater, API, and CLI call this interface instead of sequencing effects themselves.

### 4.5 Host integration adapters

The lifecycle module owns a normalized host interface. Platform variation stays behind adapters rather than leaking distro conditionals into installer, updater, setup, or UI policy.

```python
class HostPlatform:
    def inspect(self) -> HostFacts: ...
    def preflight(self, requirements: HostRequirements) -> HostReport: ...
    def converge(self, plan: HostPlan) -> HostResult: ...
```

Internal adapters cover:

- package installation: `apt`, `dnf`, and `pacman`;
- service management: systemd on supported Linux/WSL2;
- container runtime and rootful/rootless capability checks;
- bare-metal, VM, LXC, and WSL environment facts;
- firewall/network integration where supported;
- device-node and group permission convergence.

Adapters translate normalized operations such as “ensure package,” “install unit,” and “grant device access.” They do not decide which runners, profiles, slots, or models hal0 should use.

### 4.6 Existing domain stores remain authoritative

- Slot configuration remains in the canonical slot store.
- Model inventory remains in the canonical model registry/store.
- Profiles remain behind `ProfileStore`.
- Release updates remain in `src/hal0/updater/updater.py`.
- Lifecycle operational records use the existing hal0 SQLite database rather than a new JSON state file.
- Secrets live in root-owned integration files and are referenced from ordinary configuration.

The lifecycle modules coordinate existing stores; they do not replace them.

## 5. Catalog envelope and compatibility

```toml
schema_version = 1
catalog_version = "1.0.0"
release = "1.0.0-rc1"
generated_format = "canonical-json-v1"
```

Compatibility rules:

- Unknown required fields fail catalog validation.
- Unknown optional fields are ignored only when their namespace declares forward compatibility.
- The runtime accepts the current schema and one immediately previous schema during a release migration window.
- Catalog activation is atomic with release activation.
- Rollback restores the previous release's catalog and built-in profile view.

## 6. Package inventory shape

The package inventory represents the complete reviewed set of container packages published by Hal0ai for the release, including packages that are not selectable inference runners.

```python
PackageDefinition(
    id: PackageId,
    display_name: str,
    repository: str,
    digest: Sha256Digest,
    platforms: tuple[Platform, ...],
    package_kind: PackageKind,
    channel: ReleaseChannel,
    source_repository: str,
    source_revision: str,
    size_bytes: int | None,
    published_at: datetime | None,
    deprecated: bool,
    replacement: PackageId | None,
)
```

`PackageKind` distinguishes `runner`, `service`, `toolbox`, `ui`, `migration`, and other release-owned images. Only packages referenced by a valid `RunnerDefinition` appear in slot runner dropdowns.

Release CI compares the authored inventory with the Hal0ai organization package inventory using authenticated GitHub APIs. Every visible container package must be represented or explicitly excluded by a reviewed release-policy rule. Runtime installation does not contact GitHub to discover packages.

Invariants:

- package IDs and repository/digest pairs are unique;
- every image uses an immutable `sha256:` digest;
- every platform has an architecture and OS;
- deprecated packages identify a replacement or an explicit terminal status;
- a runner cannot reference a package whose `package_kind` is not `runner`.

## 7. Runner definition shape

```python
RunnerDefinition(
    id: RunnerId,
    display_name: str,
    package: PackageId,
    runtime_family: RuntimeFamily,
    capabilities: frozenset[Capability],
    devices: tuple[DeviceConstraint, ...],
    backends: frozenset[Backend],
    architectures: frozenset[Architecture],
    model_formats: tuple[ModelFormatConstraint, ...],
    resource_requirements: ResourceRequirements,
    priority: int,
    default_scope: DefaultScope | None,
    environment_contract: tuple[EnvironmentField, ...],
    health_contract: HealthContract,
    provenance: Provenance,
    deprecated: bool,
    replacement: RunnerId | None,
)
```

### 7.1 Device constraint

```python
DeviceConstraint(
    kind: DeviceKind,
    vendor: str | None,
    architecture: str | None,
    minimum_driver: VersionConstraint | None,
    minimum_runtime: VersionConstraint | None,
    required_features: frozenset[str],
    preference: int,
)
```

Supported device kinds include CPU, AMD GPU, NVIDIA GPU, Intel GPU, NPU, and future accelerators. Backend values include Vulkan, ROCm/HIP, CUDA, CPU, FastFlowLM/NPU, and explicitly named future backends.

### 7.2 Model-format constraint

A model-format constraint names the container/runtime loader contract, not merely a filename extension:

```python
ModelFormatConstraint(
    container: str,
    family: str,
    quantizations: frozenset[str],
    required_metadata: frozenset[str],
    loader_revision: str | None,
)
```

Examples distinguish stock GGUF from ROCmFPX GGUF. A custom ROCmFPX tensor format cannot be assigned to a stock llama.cpp runner even though both files end in `.gguf`.

### 7.3 Defaults

There is one deterministic resolved default per supported host/device/capability request, not one mutable global tag. Validation rejects equal-priority ambiguous defaults. Resolution reasons record why a runner won.

“Latest” means the immutable digest designated by the installed release catalog. It never means pulling whatever a mutable tag points to at runtime.

## 8. Model definition shape

```python
ModelDefinition(
    id: ModelId,
    display_name: str,
    source: ModelSource,
    revision: str,
    files: tuple[ModelFile, ...],
    architecture: str,
    formats: tuple[ModelFormat, ...],
    capabilities: frozenset[Capability],
    roles: frozenset[SlotRole],
    prompt_contract: PromptContract,
    runner_constraints: RunnerSelector,
    device_constraints: tuple[DeviceConstraint, ...],
    resource_requirements: ResourceRequirements,
    license: str,
    priority: int,
    deprecated: bool,
    replacement: ModelId | None,
)
```

```python
ModelFile(
    filename: str,
    sha256: Sha256Digest,
    size_bytes: int,
    format: str,
    quantization: str,
    optional: bool,
)
```

```python
PromptContract(
    template_id: str,
    stop_tokens: tuple[str, ...],
    tool_protocol: str | None,
    parser_id: str | None,
    deterministic_tool_selection: bool,
    maximum_tool_calls_per_turn: int | None,
)
```

Repository names alone are not enough. Every automatically downloaded model requires an immutable revision, exact file list, digest, size, license, runner constraints, and prompt/tool contract.

## 9. Brain model policy

The `brain-fallback-chain` resolves in this order:

1. **Hal0 ROCmFPX FP8 Agent**

   Use `Hal0ai/hal0-brain-sft-fpx8-agent` only with a catalog-declared compatible ROCmFPX runner and device/backend. The Hugging Face repository metadata and artifacts must be corrected and pinned before the catalog can validate this entry.
2. **Hal0 stock GGUF**

   Use `Hal0ai/hal0-brain-sft-GGUF` before any third-party fallback when its F16 artifact fits and a compatible stock runner is available. The currently documented artifact is a 2.0 GB F16 GGUF with SHA-256 `ed9d28c4eac1d7c291bc80d9410c243a3d28e655921ccaf90f2b6619aa24d2c3`; release integration must additionally pin an immutable Hugging Face revision.
3. **MiniCPM5 Agentic Tooluse Q8_0**

   Use `ewinregirgojr/MiniCPM5-1B-Agentic-Tooluse-GGUF`, selecting the Q8_0 artifact, only when the first two choices cannot run or fit. Its exact revision and file digest must be catalog-pinned. Its template, EOS handling, XML tool-call protocol, deterministic decoding, schema validation, and stop-after-complete-call behavior are part of its prompt contract.

Selection returns structured rejection reasons for every earlier candidate. Setup and WebUI can therefore explain why the fallback was chosen.

No brain model is downloaded unless Hermes is installed successfully and brain setup is selected by policy/operator intent.

## 10. Built-in profile shape and ownership

```python
ProfileDefinition(
    id: ProfileId,
    display_name: str,
    ownership: Literal["builtin"],
    role: SlotRole,
    capabilities: frozenset[Capability],
    runner_policy: RunnerPolicyId,
    model_policy: ModelPolicyId | None,
    default_resources: ResourceDefaults,
    runtime_options: Mapping[str, JsonScalar],
    prompt_policy: PromptPolicyId | None,
    integration: IntegrationId | None,
    profile_version: str,
)
```

Built-in profiles are virtual release-owned records exposed through `ProfileStore`:

- they are read-only;
- they update atomically with the release catalog;
- editing one creates an operator-owned custom copy;
- custom profiles are persisted separately and never overwritten;
- existing divergent materialized built-ins are rescued once as uniquely named custom profiles before legacy entries are removed;
- rollback restores the prior built-in view automatically.

Profiles reference runner and model policies. They do not duplicate image repositories, digests, or model filenames.

The `hal0-brain` profile declares the Hermes integration, brain role, `brain-fallback-chain`, compatible runner policy, tool routing to `hal0/agent`, context/resource defaults, and health contract.

## 11. Bootstrap policy shape

```python
BootstrapPolicy(
    initial_slots: tuple[InitialSlotPolicy, ...],
    default_runner_policy: RunnerPolicyId,
    pull_default_runner: bool,
    hermes: HermesBootstrapPolicy,
    capability_scaffolding: Literal["none"],
)
```

```python
InitialSlotPolicy(
    name: str,
    role: SlotRole,
    profile: ProfileId | None,
    enabled: bool,
    model_policy: ModelPolicyId | None,
    ready_without_model: bool,
)
```

```python
HermesBootstrapPolicy(
    default_install: bool,
    detect_existing: bool,
    explicit_opt_out: bool,
    brain_slot: InitialSlotPolicy,
    model_policy: ModelPolicyId,
)
```

Release policy is:

- seed only `agent` during base installation;
- set `agent.enabled = true`;
- assign the resolved default runner pin;
- assign no model;
- represent it as `waiting_for_model`, not failed;
- create no capability scaffolds;
- detect and integrate an existing Hermes installation;
- offer Hermes installation enabled by default with explicit opt-out;
- seed `brain` only after Hermes installation/detection passes its health check;
- select and download the brain model through the fallback policy;
- leave brain disabled with an actionable readiness issue if its selected artifact cannot be downloaded or activated.

## 12. Resolution shapes

```python
ResolutionRequest(
    purpose: ResolutionPurpose,
    host: HostFacts,
    intent: OperatorIntent,
    installed: InstalledState,
    catalog_revision: str,
)
```

`HostFacts` contains normalized probe results: support tier; distro family/version; package-manager adapter; bare-metal/VM/LXC/WSL environment; privilege/user-namespace mode; init/service manager; platform and architecture; kernel; memory and storage; mount/filesystem traits; detected devices; drivers/runtimes; container capabilities; LXC/PVE signals; networking/firewall traits; and network availability. It contains no secrets.

Host facts are capability-oriented. Downstream policy asks whether a normalized feature is supported rather than branching on distro names. Raw detection evidence remains available for diagnostics.

`OperatorIntent` contains explicit choices: storage locations, bind/auth policy, Hermes opt-out, permitted downloads, selected device, and retry target.

`InstalledState` contains current release/catalog, slots, profiles, images, models, artifact jobs, integrations, readiness issues, and recommendations.

```python
ResolutionPlan(
    plan_id: str,
    purpose: ResolutionPurpose,
    catalog_revision: str,
    preconditions: tuple[Precondition, ...],
    operations: tuple[LifecycleOperation, ...],
    selections: tuple[SelectionDecision, ...],
    warnings: tuple[LifecycleNotice, ...],
    estimated_download_bytes: int,
    requires_confirmation: bool,
)
```

```python
SelectionDecision(
    subject: ResourceRef,
    selected: ResourceRef,
    reason_code: str,
    reasons: tuple[str, ...],
    rejected: tuple[RejectedCandidate, ...],
)
```

The plan is serializable and redacted. The exact same shape supports dry-run CLI output, API responses, WebUI confirmation, logs, fixtures, and application.

## 13. Operation and result shapes

`LifecycleOperation` is a closed discriminated union:

- validate precondition;
- write non-secret configuration;
- write secret reference;
- ensure directory/ownership;
- install release/unit;
- upsert/remove built-in migration record;
- create/update slot;
- install/detect integration;
- pull/verify runner;
- pull/verify model;
- activate/deactivate resource;
- create/resolve readiness issue;
- create/dismiss recommendation;
- retain/release rollback artifact.

Every operation includes:

```python
OperationMeta(
    operation_id: str,
    idempotency_key: str,
    phase: LifecyclePhase,
    fatality: Fatality,
    resource: ResourceRef,
    compensating_operation: str | None,
)
```

Results contain sanitized facts only:

```python
LifecycleResult(
    plan_id: str,
    status: ResultStatus,
    completed: tuple[OperationResult, ...],
    skipped: tuple[OperationResult, ...],
    failed: tuple[OperationResult, ...],
    readiness_issues: tuple[ReadinessIssue, ...],
    recommendations: tuple[Recommendation, ...],
    next_actions: tuple[ActionRef, ...],
)
```

## 14. Durable lifecycle state

Lifecycle operational state is transactional in the existing SQLite database.

Required logical records:

### 14.1 Catalog activation

- active catalog revision;
- prior catalog revision;
- release association;
- activation/rollback timestamps.

### 14.2 Artifact inventory

- artifact kind and catalog ID;
- exact digest/revision/file;
- verified/present/partial/missing state;
- local location or container image ID;
- first/last verification time;
- rollback-retention deadline;
- operator-owned versus release-owned provenance.

### 14.3 Lifecycle jobs

- stable job and idempotency IDs;
- operation/resource;
- queued/running/succeeded/failed/cancelled state;
- progress bytes/layers/files;
- sanitized failure code/detail;
- retry count and last attempt.

### 14.4 Readiness issues

```python
ReadinessIssue(
    id: str,
    severity: Literal["info", "warning", "error"],
    reason_code: str,
    resource: ResourceRef,
    summary: str,
    detail: str,
    retry_action: ActionRef | None,
    cli_command: str | None,
    ui_route: str | None,
    created_at: datetime,
    updated_at: datetime,
    resolved_at: datetime | None,
)
```

### 14.5 Recommendations

```python
Recommendation(
    id: str,
    kind: Literal["runner-update", "model-update", "configuration"],
    resource: ResourceRef,
    current: ResourceRef | None,
    available: ResourceRef,
    reason_code: str,
    compatibility: CompatibilityResult,
    action: ActionRef,
    catalog_revision: str,
    dismissed_for_revision: bool,
)
```

Dismissal is scoped to the exact recommendation/catalog revision. A different digest creates a new recommendation.

## 15. Install lifecycle

### 15.1 Phase A: trusted bootstrap

1. Resolve channel manifest.
2. Verify manifest schema and release policy.
3. Verify release digest and signature according to ratified production policy.
4. Extract safely into a staged versioned tree.
5. Export verified context and invoke the installer.

Trust, digest, signature, or extraction failures are fatal and make no active-release change.

### 15.2 Phase B: essential installer configuration

Before state directories, ownership, or services are created:

1. classify distro, package manager, support tier, and bare-metal/VM/LXC/WSL environment;
2. validate init, container, namespace, filesystem, network, and device prerequisites through the host adapter;
3. stop unsupported environments before mutation with remediation guidance;
4. collect:

- model-store path;
- application-state and configuration paths when configurable;
- bind address and advertised hostname;
- API port and allowed origins;
- authentication mode;
- generated or supplied hal0 API credential;
- service UID/GID behavior and ownership repair consent;
- validated secret destinations.

The installer validates writability, free space, mount behavior, UID/GID collisions, port conflicts, bind exposure, origin syntax, and authentication safety. It displays a redacted summary and requires confirmation unless a validated noninteractive answer file is supplied.

### 15.3 Phase C: base convergence

1. Create users, groups, directories, ownership, and secret locations.
2. Install release tree, shared venv, systemd units, and `hal0.target`.
3. Write non-secret configuration and secret references.
4. Activate the release catalog and virtual built-in profiles.
5. Probe hardware through the canonical hardware module.
6. Resolve the default agent runner.
7. Begin its digest-pinned pull.
8. Seed `agent` enabled, model-empty, and pinned to the selected runner.
9. Start the API/control plane without starting a model runtime for the empty agent.
10. Record any runner download failure as a readiness issue.
11. Complete successfully when the base control-plane health contract passes.

A base install performs no broad model selection, no general model downloads, and no capability-slot scaffolding.

### 15.4 Phase D: guided setup

The installer guides the operator directly into the rerunnable `hal0 setup` flow. Setup:

- confirms storage, network, authentication, and base permissions;
- optionally collects Hugging Face credentials;
- detects LXC/PVE and optionally collects Proxmox endpoint, realm/user/token ID, and token secret;
- detects existing Hermes;
- offers Hermes installation enabled by default when absent, with explicit opt-out;
- validates Hermes health and hal0 integration;
- creates `brain` only after Hermes is healthy;
- resolves, downloads, verifies, assigns, and activates the selected brain model;
- runs readiness checks and prints actionable remaining work.

Setup is safe to rerun. It repairs missing convergent state and does not duplicate slots, integrations, jobs, or secrets.

## 16. Agent and brain state semantics

Slot configuration intent and runtime readiness are separate axes.

```python
SlotIntent = "enabled" | "disabled"
SlotReadiness = (
    "ready"
    | "waiting_for_model"
    | "waiting_for_runner"
    | "waiting_for_integration"
    | "artifact_failed"
    | "configuration_error"
)
```

The initial agent is `enabled + waiting_for_model`. This is a healthy setup-required state:

- systemd does not enter a restart loop;
- routing returns a typed actionable unavailable response;
- WebUI prompts model selection;
- health reporting does not claim inference readiness;
- assigning a compatible verified model permits activation without recreating the slot.

Brain is absent unless Hermes succeeds. If brain artifact provisioning later fails, it is `disabled + artifact_failed` with a retry action.

## 17. Enable/disable contract

The current enable/disable feature must be audited and repaired as part of this work because optional capability slots depend on it.

- Disabled slots never autostart.
- Disabled slots are never awakened by routing, capability lookup, boot restoration, or reconciliation.
- Disabled slot units/Quadlets may exist but are not enabled in systemd.
- Reboot and daemon reload preserve disabled intent.
- Readiness failures never change operator intent silently.
- Enabling an unready slot persists enabled intent but returns its readiness state and required actions.
- Capability toggles call the same slot-domain operation as CLI/API slot enable/disable.
- API, CLI, WebUI, database/config, systemd, and container state report a consistent outcome.

## 18. Runner and model artifact behavior

### 18.1 Install

Only the resolved default agent runner is required for base install. If Hermes brain is provisioned, pull only the selected compatible brain runner when it differs and the selected brain model.

### 18.2 Update

When the new catalog changes a default runner digest:

1. verify catalog compatibility;
2. pull and verify the new digest;
3. retain the prior digest through the rollback window;
4. activate the new release/catalog;
5. do not alter any existing slot runner pin;
6. create per-slot recommendations where the new runner is compatible;
7. show the new runner as available in slot create/edit.

If the new default pull fails, the code/config update may still complete when the existing control plane and all currently pinned slot runners remain coherent. The failure becomes a readiness issue with retry actions.

### 18.3 Cleanup

Automatic cleanup removes release-owned unreferenced artifacts only after:

- the rollback retention deadline has passed;
- no slot/profile/job references the artifact;
- no active or staged release requires it;
- artifact provenance is known.

Unknown and operator-owned images/models are never deleted automatically.

## 19. Built-in profile update behavior

Install and update both activate the release's built-in profile catalog.

Migration of current materialized profiles:

1. compare each legacy seed with the release-owned baseline it originated from;
2. delete an unchanged materialized copy because the virtual built-in replaces it;
3. rescue a divergent copy under a collision-free custom name;
4. preserve its behavior and provenance;
5. record migration completion so reruns do not create additional copies.

After migration, built-ins cannot be edited. “Edit” becomes “duplicate as custom.” Existing custom profiles are never merged or rewritten.

## 20. Slot create/edit experience

The create/edit flow asks for device/backend before runner selection.

The runner dropdown uses a resolver response, not client-side matching. Each row shows:

- runner display name and runtime family;
- recommended/default marker;
- installed, pulling, available, deprecated, or unavailable state;
- backend/device compatibility;
- supported capabilities and model formats;
- current digest/version and newer catalog digest/version;
- approximate download size;
- concise incompatibility reason.

New compatible runners receive an orange attention border plus icon and text; color is never the only signal. Existing slot pins remain selected. Applying an upgrade requires explicit confirmation and model-format revalidation.

Attention clears only when the operator:

- upgrades the slot;
- explicitly dismisses that exact catalog recommendation; or
- changes slot/device/model state so the recommendation no longer applies.

## 21. Readiness and retry experience

Readiness issues are first-class domain records. CLI and WebUI render the same records.

Examples:

```text
Default runner download incomplete.
Retry: hal0 artifacts retry runner-pull:<digest>
Open: /settings/runners
```

```text
Brain model verification failed.
Retry: hal0 artifacts retry model-pull:<model-id>:<revision>
Open: /slots/brain
```

WebUI provides:

- a global warning summary;
- resource-local orange attention treatment;
- sanitized error details;
- progress when a job is active;
- one-click retry;
- copyable CLI command;
- resolution confirmation after verification.

Retries reuse the same idempotency key, resume when supported, re-verify the final artifact, and resolve the issue transactionally.

## 22. Configuration and secret handling

### 22.1 Non-secret configuration

Ordinary configuration stores:

- storage paths;
- bind/hostname/ports/origins;
- authentication mode;
- integration enabled/present state;
- secret references;
- selected policies and operator intent.

### 22.2 Secret files

```text
/etc/hal0/secrets/api-auth.env
/etc/hal0/secrets/huggingface.env
/etc/hal0/secrets/proxmox.env
```

Each file is:

- root-owned;
- mode `0600`;
- written atomically;
- readable only by the exact service through a controlled credential/environment seam;
- rotated independently;
- preserved by conservative uninstall;
- removed by `--purge` after confirmation.

Configuration contains a secret reference and presence metadata, never the value.

### 22.3 Setup fields

Essential installer fields:

- model store;
- state/config locations where supported;
- bind address;
- advertised hostname;
- API port;
- allowed origins;
- authentication mode;
- generated/provided hal0 auth credential.

Advanced setup fields:

- Hugging Face token;
- detected Proxmox endpoint;
- Proxmox realm/user;
- token ID;
- token secret;
- integration test/skip decision.

Proxmox questions appear only when LXC/PVE signals exist or the operator explicitly enables the integration.

Answer files may reference environment-variable names or precreated secret-file paths. Exported answer files never contain secret values by default.

### 22.4 Redaction

Redaction occurs before logging and serialization. Plans, results, readiness issues, audit events, support bundles, exceptions, and API responses contain only secret name, configured status, last validation time, and masked suffix when safe.

## 23. Existing-install migration

Migration is plan-first, dry-runnable, convergent, and backup-aware.

It will:

- map known existing image pins to runner catalog IDs while preserving exact digests;
- preserve unknown/custom image pins as operator-owned external runner references;
- activate virtual built-in profiles;
- rescue divergent legacy built-ins as custom profiles;
- remove untouched empty capability scaffolds only when they have no model, jobs, state, custom configuration, or enabled unit;
- preserve every configured/stateful slot;
- migrate existing HF, API-auth, and Proxmox secret values into separate secret files;
- update ordinary configuration to secret references;
- maintain compatibility readers for one release window;
- write only the new shape after migration;
- produce a backup manifest and explicit rollback limitations.

No migration runs implicitly before its runtime can read both old and new shapes.

## 24. Updater integration and rollback

`src/hal0/updater/updater.py` remains the canonical updater.

The updater:

1. verifies and stages release/catalog artifacts;
2. validates the catalog independently of the active release;
3. asks the lifecycle resolver for `UpdatePlan`;
4. pre-pulls the new default runner when possible;
5. prepares profile/secret/state migrations;
6. applies migrations under existing updater gates;
7. atomically switches release and catalog;
8. restarts only required services;
9. records the previous release/catalog and rollback artifacts;
10. publishes readiness issues and recommendations.

Rollback restores release code, built-in catalog/profile view, symlink, venv, and restart state. It does not delete verified newer artifacts. Forward-only data migrations must declare backup-required or rollback-blocked policy before apply.

## 25. API and CLI interface

The public lifecycle operations are domain verbs, not installer internals:

- inspect lifecycle readiness;
- preview setup/update convergence;
- apply confirmed convergence;
- list compatible runners for a slot/device/model;
- retry/cancel artifact work;
- accept/dismiss a recommendation;
- inspect secret configured/validation status;
- rotate an integration secret.

CLI mirrors these verbs and supports human and stable JSON output. It prints exact retry commands from `ActionRef`; callers do not assemble commands from free text.

Streaming artifact progress uses the existing authenticated stream helper and never includes credentials or signed URLs.

## 26. Performance design

- Bundled catalogs are parsed and validated once per process/revision, then held as immutable indexed structures.
- Resolver indexes by capability, device kind, backend, architecture, model format, and role; slot dropdown resolution does not scan package registries or contact networks.
- Catalog comparison is linear in catalog plus installed resources and runs once per staged update.
- Artifact probes are bounded and cached; list screens do not synchronously inspect every container image.
- Pulls are deduplicated by digest and shared across slots/jobs.
- Default pull concurrency is conservative to avoid saturating disk/network; metadata checks may run concurrently.
- Progress writes are rate-limited/coalesced while terminal transitions remain transactional.
- API list operations paginate full package inventory but return the small compatible runner set for slot editing.
- Hardware and host-platform probing are reused from canonical cached probe state unless the operator explicitly refreshes them.
- Package-manager adapters batch package queries/installations rather than spawning one transaction per package.
- Distro/environment conditionals remain inside host adapters, preserving a constant resolver interface and indexed compatibility lookup.
- Startup never blocks on remote package/model discovery or optional artifact downloads.

Performance acceptance surfaces:

- cached compatible-runner resolution is interactive and does no I/O;
- repeated install/setup/update previews produce no side effects;
- a second identical apply performs no duplicate pull or write;
- WebUI readiness rendering requires one bounded readiness query, not one query per resource.

## 27. Error and fatality policy

Fatal base-install/update errors:

- release/catalog trust failure;
- unsafe extraction;
- invalid catalog or unresolved required references;
- unsupported host architecture;
- state-directory or secret-permission failure;
- migration refusal;
- atomic activation failure;
- unhealthy base API/control plane after compensating restart.

Nonfatal actionable errors:

- runner/model network timeout;
- remote rate limit;
- resumable partial download;
- optional Hermes installation failure;
- brain model incompatibility/download failure;
- optional Proxmox validation failure.

A nonfatal error cannot be silently swallowed. It must produce a durable issue and exact retry or correction action.

## 28. Uninstall behavior

Conservative uninstall removes code, units, generated runtime artifacts, and managed venvs while preserving:

- operator configuration;
- custom profiles and slots;
- model store;
- lifecycle records needed for reinstall;
- secret files;
- verified models/images unless explicitly selected for removal.

`--purge` removes the declared hal0-owned state, secrets, users/groups when safe, and release-owned artifacts after confirmation. Unknown/operator-owned artifacts remain protected unless named explicitly.

Reinstall consumes preserved state through the same resolver and converger; it does not recreate deleted scaffolds.

## 29. Validation strategy

The lifecycle resolver/converger interface is the primary test surface.

### 29.1 Catalog tests

- schema compatibility;
- complete package inventory or explicit reviewed exclusion;
- immutable image/model references;
- cross-reference integrity;
- unique deterministic defaults;
- no incompatible format/runner pairing;
- complete prompt/tool contracts;
- deprecation/replacement integrity;
- stable canonical compilation.

### 29.2 Host-platform tests

- Debian/Ubuntu `apt`, Fedora/RHEL-compatible `dnf`, and Arch-compatible `pacman` adapters;
- bare-metal, VM, privileged LXC, unprivileged LXC, supported WSL2, and refused WSL1/non-systemd WSL fixtures;
- capability-oriented equivalence across distro families;
- package transaction batching and idempotence;
- systemd enable/start/no-start/reboot behavior;
- rootful/rootless container and device-permission differences;
- unsupported-host refusal before filesystem mutation;
- diagnostics containing support tier, normalized facts, raw evidence, and remediation.

### 29.3 Resolution tests

- CPU, Vulkan, ROCm, ROCmFPX, CUDA, NPU, and mixed-device hosts;
- deterministic default runner selection;
- brain fallback order and rejection reasons;
- storage/memory fit behavior;
- existing Hermes, new Hermes, opt-out, and failed Hermes;
- existing custom pin preservation;
- installed/new catalog comparison;
- recommendation dismissal scoping.

### 29.4 Convergence tests

- fresh base install seeds only agent;
- agent is enabled and waiting for model without restart loops;
- no capability scaffolds or broad models appear;
- brain exists only after healthy Hermes;
- offline install succeeds with readiness issues;
- retries are idempotent and resolve issues;
- built-in profiles update and divergent legacy profiles are rescued once;
- existing slots never switch runner pins during update;
- old runner retained for rollback and later safely collected;
- rollback restores release/catalog/profile state;
- conservative uninstall/reinstall and purge/reinstall.

### 29.5 Security tests

- secret file owner/mode and atomic replacement;
- minimum service access;
- answer-file and API redaction;
- exception/log/support-bundle redaction;
- auth required for lifecycle mutations and progress streams;
- bind/origin/auth safety validation;
- Proxmox and HF tokens never enter ordinary configuration.

### 29.6 Enable/disable contract tests

- persistence through API, CLI, and WebUI;
- systemd enablement and reboot behavior;
- no routing or wake-on-request for disabled slots;
- enabled/unready state behavior;
- capability-toggle parity;
- daemon-reload and ghost-unit prevention.

### 29.7 UI tests

- device-first compatible dropdown;
- installed/available/incompatible/deprecated states;
- explicit runner upgrade confirmation;
- orange attention plus accessible icon/text;
- durable WebUI alerts and retry progress;
- secret masking and rotation;
- production-shaped error/refusal fixtures.

### 29.8 Live matrix

Run production evidence on privileged halo150 and unprivileged halo143, plus representative bare-metal/VM and distro-family fixtures:

- signed fresh install;
- essential collection and permission creation;
- default runner pull;
- enabled/empty agent behavior;
- Hermes opt-out and default-install paths;
- compatible brain fallback/download;
- offline/degraded retry path;
- update with new runner recommendation and no slot auto-switch;
- reboot restoration;
- explicit rollback;
- conservative uninstall/reinstall;
- purge/reinstall;
- no ghost slots after daemon reload/reboot;
- install/update/uninstall parity on apt, dnf, and pacman families;
- supported WSL2 install, restart, update, and documented limitation behavior;
- refusal and guidance on unsupported WSL configurations.

Development bootstrap/deploy evidence remains separate from production release-install evidence.

### 29.9 CI economy and test cleanup

CI is layered to avoid a distro/environment/device Cartesian product:

1. **Fast required contract suite**

   Run catalog validation, pure resolver matrices, migration plans, readiness/action serialization, and host-adapter contract tests with in-memory adapters on every change.
2. **Representative required integration matrix**

   Run one current image for each package-manager family (`apt`, `dnf`, `pacman`) plus one unprivileged LXC-shaped fixture. Exercise install essentials, package/service convergence, no-start, update, and uninstall through normalized interfaces—not the full model/runtime suite.
3. **Targeted capability jobs**

   Run GPU/NPU/ROCmFPX/WSL jobs only when matching code/catalog paths change or as scheduled hardware validation. Capability correctness is primarily covered by pure resolver fixtures; hardware jobs prove adapters and runtime integration.
4. **Release/live gate**

   Run the complete halo150/halo143 lifecycle and the supported WSL2 smoke before an RC, not on every commit.

The overhaul replaces shallow tests rather than layering new suites indefinitely:

- delete tests whose only purpose is to pin duplicated Bash constants, static slot rosters, or UI-side compatibility logic after those owners are removed;
- collapse per-distro copies into one host-adapter contract suite plus adapter-specific fixtures;
- replace internal implementation tests with resolver/converger interface tests where observable behavior is equivalent;
- retain focused tests for adapter-specific parsing, commands, and failure translation;
- remove obsolete snapshots and fixtures in the same slice that removes their production seam;
- track required-job duration and fixture count, with unexplained growth treated as a review finding;
- cache package metadata and build artifacts, but never cache trust-verification results that must be evaluated per release.

A new required CI job needs a distinct failure domain that cannot be covered in an existing job. Otherwise its assertions belong in an existing contract or integration suite.

## 30. Release gates

Release checks fail when:

- a package/runner image uses a mutable tag as authority;
- a model lacks immutable revision, exact file, digest, size, or license;
- GitHub package inventory and reviewed catalog coverage diverge;
- a runner/profile/model cross-reference is invalid;
- a host/device/capability default is ambiguous;
- a custom model format can resolve to an incompatible runner;
- generated catalog output differs from authored catalog input;
- installer/setup/API/CLI/UI contract fixtures disagree;
- a required lifecycle matrix row is missing, stale, skipped, or deferred;
- new duplicate platform tests bypass the normalized host-adapter contract without a documented distinct failure domain.

## 31. Delivery decomposition

### 31.1 Active coordination constraint

A separate session owns existing PR #1330 CI cleanup under `/ci-pr1330-repair/`. This overhaul must not edit CI workflow/configuration or perform broad existing-test cleanup until that work is complete and integrated. Before the CI-consolidation slice, rebase/reconcile against the landed CI repair, inventory its final required jobs, and preserve its fixes. Earlier lifecycle slices may add narrowly targeted local tests needed for TDD, but CI wiring and deletion/consolidation of existing suites are deferred to the final dedicated slice.

### 31.2 Ordered slices

Implementation should proceed as independently reviewable vertical slices:

1. Catalog schema/compiler/validator and release checks.
2. Deep resolver interface with hardware/model/runner compatibility.
3. Virtual immutable built-in profiles and divergent-profile rescue.
4. Transactional lifecycle state, plans, issues, actions, and recommendations.
5. Base installer essential collection and minimal agent convergence.
6. Default runner pull, degraded readiness, and retry flow.
7. Hermes/brain conditional convergence and three-level model fallback.
8. Updater comparison, pre-pull, recommendations, retention, and rollback.
9. Slot enable/disable contract repair and capability opt-in.
10. Slot create/edit compatible runner UX and attention state.
11. Secret migration and setup collection.
12. Uninstall/reinstall contracts and two-host release validation.
13. CI consolidation: remove superseded shallow tests, collapse platform duplication into adapter contracts, and document the final required/scheduled/release job split.

Each slice uses test-driven development and gets independent spec/quality review before the next writer task. Every slice that deepens a seam deletes or rewrites the shallow tests it supersedes; test count is not treated as a quality goal.

## 32. Success criteria

The overhaul is complete when:

- policy no longer exists in duplicated Bash loops, static slot rosters, setup constants, and independent UI matching;
- a single validated catalog describes every reviewed Hal0ai package and every selectable runner;
- installer, setup, updater, API, CLI, and UI consume the same typed plans/results;
- fresh install creates only an enabled/model-empty agent plus a conditional Hermes brain;
- the default runner is digest-pinned and pulled without making network failure fatal;
- no general model or capability scaffold is installed initially;
- built-in profiles update atomically while custom profiles remain untouched;
- existing slots never change runner pins automatically;
- new compatible runners are clearly actionable in UI and CLI;
- every failed optional artifact operation has a durable retry path;
- secrets are isolated, redacted, migratable, and purge-aware;
- apt, dnf, and pacman hosts use one normalized lifecycle interface across bare metal, VM, and supported containers;
- supported WSL2 installs use the same configuration/catalog contracts while exposing explicit environment limitations;
- unsupported hosts are refused before mutation with actionable remediation;
- disabled slots remain disabled across routing, reconciliation, systemd, and reboot;
- rollback, conservative reinstall, purge reinstall, and ghost-slot prevention pass on both live host classes;
- per-change CI uses a bounded representative matrix rather than every distro/environment/device combination;
- obsolete shallow and duplicate tests are removed as their production owners disappear;
- the resulting external lifecycle interface is smaller than the policy it replaces.
