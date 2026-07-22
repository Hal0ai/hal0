"""Immutable shapes for the release-owned lifecycle catalog."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Sha256Image = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
Sha256File = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ImmutableRevision = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PackageDefinition(FrozenModel):
    id: str
    repository: str
    digest: Sha256Image
    package_kind: Literal["runner", "service", "toolbox", "ui", "migration"]
    platforms: tuple[str, ...]
    deprecated: bool = False
    replacement: str | None = None
    terminal: bool = False


class RunnerDefinition(FrozenModel):
    id: str
    package: str
    runtime_family: str
    capabilities: frozenset[str]
    hosts: frozenset[str]
    backends: frozenset[str]
    architectures: frozenset[str]
    model_formats: frozenset[str]
    priority: int = 0
    default_for: tuple[str, ...] = ()
    deprecated: bool = False
    replacement: str | None = None


class ModelFile(FrozenModel):
    filename: str = Field(min_length=1)
    sha256: Sha256File
    size_bytes: int = Field(gt=0)
    format: str = Field(min_length=1)
    quantization: str = Field(min_length=1)
    optional: bool = False


class PromptContract(FrozenModel):
    template_id: str = Field(min_length=1)
    stop_tokens: tuple[str, ...]
    tool_protocol: str | None = None
    parser_id: str | None = None
    deterministic_tool_selection: bool
    maximum_tool_calls_per_turn: int | None = Field(default=None, gt=0)
    validate_tool_schema: bool = False
    stop_after_complete_call: bool = False


class ModelDefinition(FrozenModel):
    id: str
    source: str
    revision: ImmutableRevision
    files: tuple[ModelFile, ...]
    architecture: str
    formats: frozenset[str]
    capabilities: frozenset[str]
    roles: frozenset[str]
    prompt_contract: PromptContract
    runners: tuple[str, ...]
    license: str = Field(min_length=1)
    priority: int = 0
    deprecated: bool = False
    replacement: str | None = None


class ProfileDefinition(FrozenModel):
    id: str
    ownership: Literal["builtin"]
    role: str
    capabilities: frozenset[str]
    runner_policy: str
    model_policy: str | None = None
    runtime_options: dict[str, Any] = Field(default_factory=dict)
    profile_version: str
    integration: str | None = None


class InitialSlotPolicy(FrozenModel):
    name: str
    role: str
    profile: str | None = None
    enabled: bool
    model_policy: str | None = None
    ready_without_model: bool


class HermesBootstrapPolicy(FrozenModel):
    default_install: bool
    detect_existing: bool
    explicit_opt_out: bool
    brain_slot: InitialSlotPolicy
    model_policy: str


class BootstrapPolicy(FrozenModel):
    initial_slots: tuple[InitialSlotPolicy, ...]
    default_runner_policy: str
    pull_default_runner: bool
    hermes: HermesBootstrapPolicy
    capability_scaffolding: Literal["none"]


class CatalogEnvelope(FrozenModel):
    schema_version: int
    catalog_version: str
    release: str
    generated_format: Literal["canonical-json-v1"]
    packages: tuple[PackageDefinition, ...]
    runners: tuple[RunnerDefinition, ...]
    models: tuple[ModelDefinition, ...]
    profiles: tuple[ProfileDefinition, ...]
    runner_policies: dict[str, tuple[str, ...]]
    model_policies: dict[str, tuple[str, ...]]
    bootstrap: BootstrapPolicy


class CompatibilityResult(FrozenModel):
    compatible: bool
    reason_code: str
    detail: str = ""


class CatalogReport(FrozenModel):
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


# ── Resolution domain types ──────────────────────────────────────────────────


class HostFacts(FrozenModel):
    """Resolved host identity used for runner/model selection."""

    host: str
    device_class: str
    backend: str | None = None
    architectures: frozenset[str] = frozenset({"amd64"})


class OperatorIntent(FrozenModel):
    """What the operator wants to accomplish."""

    capabilities: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()
    purpose: str | None = None


class SlotState(FrozenModel):
    """A single installed slot record snapshot."""

    name: str
    role: str
    profile: str | None = None
    runner: str | None = None
    model: str | None = None
    enabled: bool = True


class InstalledState(FrozenModel):
    """Snapshot of currently installed slots and runners."""

    slots: tuple[SlotState, ...] = ()
    runners: frozenset[str] = frozenset()


class ResourceRef(FrozenModel):
    """A named resource reference by kind and id."""

    kind: str
    id: str


class RejectedCandidate(FrozenModel):
    """A candidate that was considered but rejected, with a reason."""

    id: str
    reason_code: str
    detail: str = ""


class SelectionDecision(FrozenModel):
    """The outcome of a named selection path (e.g. "agent.runner" or "brain.model")."""

    path: str
    selected: ResourceRef | None = None
    rejected: tuple[RejectedCandidate, ...] = ()


class LifecycleOperation(FrozenModel):
    """A single lifecycle action to be executed."""

    kind: str
    resource: ResourceRef | None = None
    detail: str = ""


class ResolutionPlan(FrozenModel):
    """The full plan produced by resolution."""

    operations: tuple[LifecycleOperation, ...] = ()
    selections: tuple[SelectionDecision, ...] = ()
    rejections: tuple[RejectedCandidate, ...] = ()
    warnings: tuple[str, ...] = ()
    download_estimate_bytes: int = 0

    def selection(self, path: str) -> SelectionDecision:
        for sel in self.selections:
            if sel.path == path:
                return sel
        raise KeyError(f"no selection for path {path!r}")


class ActionRef(FrozenModel):
    """A kind+resource pair representing a planned action."""

    kind: str
    resource: ResourceRef | None = None


class UpdatePlan(FrozenModel):
    """Diff plan produced by compare() — what changed relative to installed state."""

    operations: tuple[LifecycleOperation, ...] = ()
    selections: tuple[SelectionDecision, ...] = ()
    warnings: tuple[str, ...] = ()
    download_estimate_bytes: int = 0


class ResolutionRequest(FrozenModel):
    """Input to the resolution engine."""

    host: HostFacts
    intent: OperatorIntent = OperatorIntent()
    installed: InstalledState | None = None
    purpose: str | None = None

    @classmethod
    def fresh_install(cls, *, host: HostFacts) -> ResolutionRequest:
        return cls(host=host, intent=OperatorIntent(), purpose="fresh_install")

    @classmethod
    def setup(cls, *, host: HostFacts, intent: OperatorIntent) -> ResolutionRequest:
        return cls(host=host, intent=intent, purpose="setup")
