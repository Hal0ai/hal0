"""Load, index, validate, and compile the release lifecycle catalog."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from importlib.resources import files
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from .types import (
    CatalogEnvelope,
    CatalogReport,
    CompatibilityResult,
    HostFacts,
    InstalledState,
    LifecycleOperation,
    ModelDefinition,
    PackageDefinition,
    RejectedCandidate,
    ResolutionPlan,
    ResolutionRequest,
    ResourceRef,
    RunnerDefinition,
    SelectionDecision,
    UpdatePlan,
)

_DOCUMENT_NAMES = ("packages", "runners", "models", "profiles", "bootstrap")
_METADATA_FIELDS = ("schema_version", "catalog_version", "release", "generated_format")
_DOCUMENT_FIELDS = {
    "packages": {"packages", "reviewed_exclusions"},
    "runners": {"runners"},
    "models": {"models"},
    "profiles": {"profiles", "runner_policies", "model_policies"},
    "bootstrap": {"bootstrap"},
}


class CatalogError(ValueError):
    """The catalog cannot safely be loaded or compiled."""


class LifecycleCatalog:
    """One immutable catalog with pre-built indexes and a small query surface."""

    def __init__(self, envelope: CatalogEnvelope) -> None:
        self.envelope = envelope
        self._packages = MappingProxyType({item.id: item for item in envelope.packages})
        self._runners = MappingProxyType({item.id: item for item in envelope.runners})
        self._models = MappingProxyType({item.id: item for item in envelope.models})
        self._profiles = MappingProxyType({item.id: item for item in envelope.profiles})
        self._host_runners: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
            {
                host: tuple(sorted(r.id for r in envelope.runners if host in r.hosts))
                for host in sorted({h for r in envelope.runners for h in r.hosts})
            }
        )
        self._capability_runners: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
            {
                cap: tuple(sorted(r.id for r in envelope.runners if cap in r.capabilities))
                for cap in sorted({c for r in envelope.runners for c in r.capabilities})
            }
        )
        self._format_runners: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
            {
                fmt: tuple(sorted(r.id for r in envelope.runners if fmt in r.model_formats))
                for fmt in sorted({f for r in envelope.runners for f in r.model_formats})
            }
        )

    @classmethod
    def from_documents(cls, documents: Mapping[str, Mapping[str, Any]]) -> LifecycleCatalog:
        """Parse the five authored documents into one indexed catalog."""
        missing = sorted(set(_DOCUMENT_NAMES) - set(documents))
        extra = sorted(set(documents) - set(_DOCUMENT_NAMES))
        if missing or extra:
            raise CatalogError(f"catalog documents mismatch: missing={missing}, extra={extra}")
        for name in _DOCUMENT_NAMES:
            allowed = set(_METADATA_FIELDS) | _DOCUMENT_FIELDS[name]
            unknown = sorted(set(documents[name]) - allowed)
            if unknown:
                raise CatalogError(f"{name} document has unknown fields: {unknown}")

        metadata = {field: documents["packages"].get(field) for field in _METADATA_FIELDS}
        metadata_errors = [
            f"{name}.{field} differs from packages.{field}"
            for name in _DOCUMENT_NAMES[1:]
            for field in _METADATA_FIELDS
            if documents[name].get(field) != metadata[field]
        ]
        if metadata_errors:
            raise CatalogError("catalog envelope mismatch: " + "; ".join(metadata_errors))

        payload = {
            **metadata,
            "packages": documents["packages"].get("packages", ()),
            "runners": documents["runners"].get("runners", ()),
            "models": documents["models"].get("models", ()),
            "profiles": documents["profiles"].get("profiles", ()),
            "runner_policies": documents["profiles"].get("runner_policies", {}),
            "model_policies": documents["profiles"].get("model_policies", {}),
            "bootstrap": documents["bootstrap"].get("bootstrap"),
        }
        try:
            return cls(CatalogEnvelope.model_validate(payload))
        except ValidationError as exc:
            raise CatalogError(f"catalog schema invalid: {exc}") from exc

    @classmethod
    def from_compiled(cls, document: Mapping[str, Any]) -> LifecycleCatalog:
        """Load the already-compiled runtime representation."""
        try:
            return cls(CatalogEnvelope.model_validate(document))
        except ValidationError as exc:
            raise CatalogError(f"compiled catalog schema invalid: {exc}") from exc

    @classmethod
    def load_bundled(cls) -> LifecycleCatalog:
        """Load only the canonical JSON owned by the installed release."""
        resource = files("hal0.lifecycle.data").joinpath("catalog.json")
        try:
            document = json.loads(resource.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise CatalogError(f"bundled catalog unavailable: {exc}") from exc
        return cls.from_compiled(document)

    def canonical_json(self) -> str:
        """Return byte-stable canonical JSON, including one trailing newline."""

        def normalize(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            if isinstance(value, frozenset):
                return [normalize(item) for item in sorted(value)]
            if isinstance(value, tuple | list):
                return [normalize(item) for item in value]
            return value

        payload = normalize(self.envelope.model_dump(mode="python"))
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"

    def package(self, package_id: str) -> PackageDefinition:
        try:
            return self._packages[package_id]
        except KeyError as exc:
            raise CatalogError(f"unknown package: {package_id}") from exc

    def runner(self, runner_id: str) -> RunnerDefinition:
        try:
            return self._runners[runner_id]
        except KeyError as exc:
            raise CatalogError(f"unknown runner: {runner_id}") from exc

    def model(self, model_id: str) -> ModelDefinition:
        try:
            return self._models[model_id]
        except KeyError as exc:
            raise CatalogError(f"unknown model: {model_id}") from exc

    def default_runner(self, *, host: str, capability: str) -> RunnerDefinition:
        scope = f"{host}/{capability}"
        candidates = [runner for runner in self.envelope.runners if scope in runner.default_for]
        if not candidates:
            raise CatalogError(f"no default runner for {scope}")
        priority = max(item.priority for item in candidates)
        winners = sorted(
            (item for item in candidates if item.priority == priority), key=lambda item: item.id
        )
        if len(winners) != 1:
            raise CatalogError(
                f"ambiguous default runner for {scope}: {[item.id for item in winners]}"
            )
        return winners[0]

    def compatibility(self, *, model: str, runner: str) -> CompatibilityResult:
        model_definition = self._models.get(model)
        if model_definition is None:
            return CompatibilityResult(compatible=False, reason_code="model.unknown", detail=model)
        runner_definition = self._runners.get(runner)
        if runner_definition is None:
            return CompatibilityResult(
                compatible=False, reason_code="runner.unknown", detail=runner
            )
        if model_definition.formats.isdisjoint(runner_definition.model_formats):
            return CompatibilityResult(
                compatible=False,
                reason_code="model_format.unsupported",
                detail=f"{sorted(model_definition.formats)} not supported by {runner}",
            )
        if runner not in model_definition.runners:
            return CompatibilityResult(
                compatible=False,
                reason_code="runner.unsupported",
                detail=f"{model} does not permit {runner}",
            )
        return CompatibilityResult(compatible=True, reason_code="compatible")

    def validate(self) -> CatalogReport:
        """Return all semantic errors in stable order without mutating catalog state."""
        for runner in self.envelope.runners:
            if ":latest" in runner.package or ":" in runner.package or "@" in runner.package:
                raise CatalogError(
                    f"runner {runner.id!r} package must reference a catalog ID with an immutable digest"
                )

        errors: set[str] = set()
        self._validate_unique_ids(errors)
        self._validate_packages(errors)
        self._validate_runners(errors)
        self._validate_models(errors)
        self._validate_profiles(errors)
        self._validate_bootstrap(errors)
        return CatalogReport(errors=tuple(sorted(errors)))

    def _validate_unique_ids(self, errors: set[str]) -> None:
        groups = {
            "package": [item.id for item in self.envelope.packages],
            "runner": [item.id for item in self.envelope.runners],
            "model": [item.id for item in self.envelope.models],
            "profile": [item.id for item in self.envelope.profiles],
        }
        for kind, identifiers in groups.items():
            for identifier, count in Counter(identifiers).items():
                if count > 1:
                    errors.add(f"{kind} ID {identifier!r} is duplicated")

    def _validate_packages(self, errors: set[str]) -> None:
        pairs: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
        for package in self.envelope.packages:
            pairs[(package.repository, package.digest)].append(package.id)
            if not package.platforms or any("/" not in platform for platform in package.platforms):
                errors.add(f"package {package.id!r} must declare OS/architecture platforms")
            if package.deprecated:
                if package.replacement and package.replacement not in self._packages:
                    errors.add(
                        f"package {package.id!r} replacement {package.replacement!r} does not exist"
                    )
                if not package.replacement and not package.terminal:
                    errors.add(
                        f"inactive package {package.id!r} needs replacement or terminal status"
                    )
        for pair, identifiers in pairs.items():
            if len(identifiers) > 1:
                errors.add(
                    f"package repository/digest pair {pair!r} is duplicated by {sorted(identifiers)}"
                )

    def _validate_runners(self, errors: set[str]) -> None:
        scopes: defaultdict[str, list[RunnerDefinition]] = defaultdict(list)
        supported_scopes: set[str] = set()
        for runner in self.envelope.runners:
            supported_scopes.update(
                f"{host}/{capability}"
                for host in runner.hosts
                for capability in runner.capabilities
            )
            package = self._packages.get(runner.package)
            if package is None:
                errors.add(f"runner {runner.id!r} references missing package {runner.package!r}")
            elif package.package_kind != "runner":
                errors.add(
                    f"runner {runner.id!r} package {runner.package!r} is not package_kind runner"
                )
            if not runner.model_formats:
                errors.add(f"runner {runner.id!r} has no model formats")
            if runner.deprecated and runner.replacement not in self._runners:
                errors.add(
                    f"runner {runner.id!r} replacement {runner.replacement!r} does not exist"
                )
            for scope in runner.default_for:
                scopes[scope].append(runner)
                try:
                    host, capability = scope.split("/", 1)
                except ValueError:
                    errors.add(f"runner {runner.id!r} has malformed default scope {scope!r}")
                    continue
                if host not in runner.hosts or capability not in runner.capabilities:
                    errors.add(
                        f"runner {runner.id!r} default scope {scope!r} exceeds its constraints"
                    )
        for scope in sorted(supported_scopes - scopes.keys()):
            errors.add(f"supported scope {scope!r} has no default runner")
        for scope, candidates in scopes.items():
            highest = max(item.priority for item in candidates)
            winners = [item.id for item in candidates if item.priority == highest]
            if len(winners) != 1:
                errors.add(
                    f"default scope {scope!r} is ambiguous at priority {highest}: {sorted(winners)}"
                )

    def _validate_models(self, errors: set[str]) -> None:
        for model in self.envelope.models:
            if not model.files:
                errors.add(f"model {model.id!r} has no files")
            for model_file in model.files:
                if model_file.format not in model.formats:
                    errors.add(
                        f"model {model.id!r} file format {model_file.format!r} is not declared"
                    )
            if model.deprecated and model.replacement not in self._models:
                errors.add(f"model {model.id!r} replacement {model.replacement!r} does not exist")
            for runner_id in model.runners:
                runner = self._runners.get(runner_id)
                if runner is None:
                    errors.add(f"model {model.id!r} references missing runner {runner_id!r}")
                elif model.formats.isdisjoint(runner.model_formats):
                    errors.add(
                        f"model {model.id!r} format is incompatible with runner {runner_id!r}"
                    )
            prompt = model.prompt_contract
            if prompt.tool_protocol and not prompt.parser_id:
                errors.add(f"model {model.id!r} tool prompt contract needs a parser")

    def _validate_profiles(self, errors: set[str]) -> None:
        for policy_id, runner_ids in self.envelope.runner_policies.items():
            for runner_id in runner_ids:
                if runner_id not in self._runners:
                    errors.add(
                        f"runner policy {policy_id!r} references missing runner {runner_id!r}"
                    )
        for profile in self.envelope.profiles:
            runner_ids = self.envelope.runner_policies.get(profile.runner_policy)
            if runner_ids is None:
                errors.add(
                    f"profile {profile.id!r} references missing runner policy {profile.runner_policy!r}"
                )
            if profile.model_policy and profile.model_policy not in self.envelope.model_policies:
                errors.add(
                    f"profile {profile.id!r} references missing model policy {profile.model_policy!r}"
                )
        for policy_id, model_ids in self.envelope.model_policies.items():
            for model_id in model_ids:
                if model_id not in self._models:
                    errors.add(f"model policy {policy_id!r} references missing model {model_id!r}")

    # ── Resolution ────────────────────────────────────────────────────────

    def resolve(self, request: ResolutionRequest) -> ResolutionPlan:
        """Produce a ResolutionPlan from a request. Pure, no I/O."""
        host = request.host
        operations: list[LifecycleOperation] = []
        selections: list[SelectionDecision] = []
        warnings: list[str] = []
        rejections: list[RejectedCandidate] = []

        # ── Fresh-install path ─────────────────────────────────────────
        if request.purpose == "fresh_install":
            for slot in self.envelope.bootstrap.initial_slots:
                operations.append(
                    LifecycleOperation(
                        kind="slot.ensure",
                        resource=ResourceRef(kind="slot", id=slot.name),
                        detail=f"role={slot.role}",
                    )
                )
            # Find the default runner for the default-chat policy on this host
            default_runner_id = self._resolve_default_runner(host, capability="chat")
            selections.append(
                SelectionDecision(
                    path="agent.runner",
                    selected=ResourceRef(kind="runner", id=default_runner_id),
                )
            )
            return ResolutionPlan(
                operations=tuple(operations),
                selections=tuple(selections),
                warnings=tuple(warnings),
            )

        # ── Setup path ─────────────────────────────────────────────────
        if request.purpose == "setup":
            # Brain model selection via fallback chain
            if "brain" in request.intent.roles:
                model_decision = self._resolve_brain_model(host)
                selections.append(model_decision)
                if model_decision.selected:
                    # Also select a runner for the brain model
                    runner_decision = self._resolve_runner_for_model(
                        host, model_decision.selected.id
                    )
                    selections.append(runner_decision)

            return ResolutionPlan(
                operations=tuple(operations),
                selections=tuple(selections),
                rejections=tuple(rejections),
                warnings=tuple(warnings),
            )

        return ResolutionPlan()

    def compare(self, installed: InstalledState) -> UpdatePlan:
        """Compare installed state to desired.

        Plans ``slot.ensure`` for any bundled initial slot missing from
        the installed set. Preserves existing operator runner pins —
        never emits ``slot.runner.set`` for a slot that already has a
        pinned runner. Returns an ``UpdatePlan`` for diff consumption.
        """
        operations: list[LifecycleOperation] = []
        warnings: list[str] = []

        installed_names = {slot.name for slot in installed.slots}
        for slot_policy in self.envelope.bootstrap.initial_slots:
            if slot_policy.name not in installed_names:
                operations.append(
                    LifecycleOperation(
                        kind="slot.ensure",
                        resource=ResourceRef(kind="slot", id=slot_policy.name),
                        detail=f"role={slot_policy.role}",
                    )
                )

        return UpdatePlan(
            operations=tuple(operations),
            selections=(),
            warnings=tuple(warnings),
        )

    def example_request(self, name: str) -> ResolutionRequest:
        """Create a representative request for testing and performance."""
        examples: dict[str, ResolutionRequest] = {
            "amd-vulkan": ResolutionRequest(
                host=HostFacts(host="amd-vulkan", device_class="gpu", backend="vulkan"),
                purpose="fresh_install",
            ),
        }
        return examples[name]

    # ── Internal resolution helpers ──────────────────────────────────────

    @staticmethod
    def _runner_matches_hardware(runner: RunnerDefinition, host: HostFacts) -> bool:
        if host.backend and host.backend not in runner.backends:
            return False

        gpu_backends = frozenset({"rocm", "vulkan", "cuda"})
        if not runner.backends.isdisjoint(gpu_backends):
            device_class = "gpu"
        elif runner.backends == frozenset({"cpu"}):
            device_class = "cpu"
        elif runner.backends == frozenset({"npu"}):
            device_class = "npu"
        else:
            device_class = None
        return not host.device_class or host.device_class == device_class

    def _resolve_default_runner(self, host: HostFacts, *, capability: str) -> str:
        """Find the default runner for a host/capability scope."""
        scope = f"{host.host}/{capability}"
        rdefs = [
            r
            for r in self.envelope.runners
            if scope in r.default_for
            and self._runner_matches_hardware(r, host)
            and not r.architectures.isdisjoint(host.architectures)
            and not r.deprecated
        ]
        if not rdefs:
            # Fall back to policy-based resolution with filtering
            policy = self.envelope.bootstrap.default_runner_policy
            policy_runners = list(self.envelope.runner_policies.get(policy, ()))
            host_runner_ids = set(self._host_runners.get(host.host, ()))
            cap_runner_ids = set(self._capability_runners.get(capability, ()))
            candidates = [
                rid
                for rid in (host_runner_ids & cap_runner_ids & set(policy_runners))
                if not self._runners[rid].deprecated
                and self._runner_matches_hardware(self._runners[rid], host)
                and not self._runners[rid].architectures.isdisjoint(host.architectures)
            ]
            if not candidates:
                raise CatalogError(f"no default runner for {scope}")
            # Sort by priority descending, then runner ID ascending
            candidates.sort(key=lambda rid: (-self._runners[rid].priority, rid))
            return candidates[0]
        by_priority = sorted(rdefs, key=lambda r: (-r.priority, r.id))
        return by_priority[0].id

    def _resolve_brain_model(self, host: HostFacts) -> SelectionDecision:
        """Select brain model from the brain-fallback-chain policy."""
        policy = self.envelope.bootstrap.hermes.model_policy
        model_ids = self.envelope.model_policies.get(policy, ())
        rejected: list[RejectedCandidate] = []

        # Early exit: host label not known to any runner
        host_runner_ids = self._host_runners.get(host.host, ())
        if not host_runner_ids:
            for model_id in model_ids:
                model = self._models.get(model_id)
                if model is None:
                    rejected.append(RejectedCandidate(id=model_id, reason_code="model.unknown"))
                    continue
                reason_code = self._rejection_reason(host, model)
                rejected.append(RejectedCandidate(id=model_id, reason_code=reason_code))
            return SelectionDecision(
                path="brain.model",
                selected=None,
                rejected=tuple(rejected),
            )

        for model_id in model_ids:
            model = self._models.get(model_id)
            if model is None:
                rejected.append(RejectedCandidate(id=model_id, reason_code="model.unknown"))
                continue
            # Find the best runner on this host for this model
            runner_id = self._find_host_runner_for_model(host, model)
            if runner_id is not None:
                return SelectionDecision(
                    path="brain.model",
                    selected=ResourceRef(kind="model", id=model_id),
                    rejected=tuple(rejected),
                )
            else:
                # Compute the reason code from the model's required runner list
                reason_code = self._rejection_reason(host, model)
                rejected.append(RejectedCandidate(id=model_id, reason_code=reason_code, detail=""))

        return SelectionDecision(
            path="brain.model",
            selected=None,
            rejected=tuple(rejected),
        )

    def _find_host_runner_for_model(
        self, host: HostFacts, model: ModelDefinition, *, required_capability: str = "chat"
    ) -> str | None:
        """Find the best runner on this host for a model.

        Filtering order (plan-specified):
          1. Runner host label matches HostFacts.host
          2. Runner backend and physical device class match HostFacts
          3. Runner architecture matches HostFacts.architectures
          4. Runner capability includes required_capability
          5. Runner is active
          6. Runner format intersects model formats
          7. Runner is in model's allowlist
        Then rank by runner priority descending, with deterministic tie-break
        on runner id (sorted ascending). Returns highest-ranked runner id.
        """
        host_runner_ids: tuple[str, ...] = self._host_runners.get(host.host, ())
        if not host_runner_ids:
            return None

        candidates: list[tuple[int, str]] = []
        for runner_id in host_runner_ids:
            runner = self._runners[runner_id]
            # backend and physical device-class filters
            if not self._runner_matches_hardware(runner, host):
                continue
            # architecture filter
            if runner.architectures.isdisjoint(host.architectures):
                continue
            # capability filter
            if required_capability not in runner.capabilities:
                continue
            # deprecation filter
            if runner.deprecated:
                continue
            # format compatibility
            if model.formats.isdisjoint(runner.model_formats):
                continue
            # model allowlist
            if runner_id not in model.runners:
                continue
            candidates.append((runner.priority, runner_id))

        if not candidates:
            return None

        # Sort by priority descending, then runner ID ascending (deterministic tie-break)
        candidates.sort(key=lambda x: (-x[0], x[1]))
        return candidates[0][1]

    def _rejection_reason(self, host: HostFacts, model: ModelDefinition) -> str:
        """Compute rejection reason code for a model on a host."""
        for runner_id in model.runners:
            runner = self._runners.get(runner_id)
            if runner is None:
                return f"runner.{runner_id}_missing"
            if runner_id not in self._host_runners.get(host.host, ()):
                return f"runner.{runner_id}_unavailable"
            if runner.architectures.isdisjoint(host.architectures):
                return f"runner.{runner_id}_arch_mismatch"
        return "runner.none_compatible"

    def _resolve_runner_for_model(self, host: HostFacts, model_id: str) -> SelectionDecision:
        """Select the best runner for a given model on this host."""
        model = self._models.get(model_id)
        if model is None:
            return SelectionDecision(
                path="brain.runner",
                selected=None,
                rejected=(RejectedCandidate(id=model_id, reason_code="model.unknown"),),
            )
        runner_id = self._find_host_runner_for_model(host, model)
        if runner_id:
            return SelectionDecision(
                path="brain.runner",
                selected=ResourceRef(kind="runner", id=runner_id),
            )
        return SelectionDecision(
            path="brain.runner",
            selected=None,
            rejected=(
                RejectedCandidate(
                    id=model_id,
                    reason_code=self._rejection_reason(host, model),
                ),
            ),
        )

    def _validate_bootstrap(self, errors: set[str]) -> None:
        policy = self.envelope.bootstrap
        if [slot.name for slot in policy.initial_slots] != ["agent"]:
            errors.add("bootstrap initial slots must contain only agent")
        else:
            agent = policy.initial_slots[0]
            if agent.role != "agent" or not agent.enabled or agent.model_policy is not None:
                errors.add("bootstrap agent must be enabled, role agent, and have no model policy")
        if policy.capability_scaffolding != "none":
            errors.add("bootstrap capability scaffolding must be none")
        if policy.default_runner_policy not in self.envelope.runner_policies:
            errors.add(
                f"bootstrap references missing runner policy {policy.default_runner_policy!r}"
            )
        brain = policy.hermes.brain_slot
        if brain.name != "brain" or brain.role != "brain":
            errors.add("Hermes conditional slot must be brain")
        if brain in policy.initial_slots:
            errors.add("brain must be conditional on healthy Hermes, not an initial slot")
        for slot in (*policy.initial_slots, brain):
            if slot.profile and slot.profile not in self._profiles:
                errors.add(
                    f"bootstrap slot {slot.name!r} references missing profile {slot.profile!r}"
                )
            if slot.model_policy and slot.model_policy not in self.envelope.model_policies:
                errors.add(
                    f"bootstrap slot {slot.name!r} references missing model policy {slot.model_policy!r}"
                )
        if policy.hermes.model_policy not in self.envelope.model_policies:
            errors.add(f"Hermes references missing model policy {policy.hermes.model_policy!r}")
