"""SPECIALTY_KINDS — the specialty-distribution registry.

Some model distributions ship more than a GGUF: runtime companion files,
required environment variables, and a runner built a particular way
(#1946's CIRU ActiveFPX + PromptForge is the first). This module is a
CODE registry, the same philosophy as :data:`hal0.runners.RUNNER_IMAGES`:
shipping support for a kind means shipping code anyway, so the truth
lives here — importable, testable, no database table.

A kind's ``key`` doubles as the capability token a runner image must
list in ``RunnerSupports.specialties`` to run the accelerated path.
Leaf module by design: stdlib-only imports, because
:mod:`hal0.registry.fileset` imports *this* for classification.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath


@dataclass(frozen=True, slots=True)
class CompanionSpec:
    """One companion file a specialty distribution ships."""

    role: str  # model_file.role value, e.g. "promptforge_ffn"
    pattern: re.Pattern  # filename matcher (searched, case-insensitive)
    env: str | None  # env var receiving the installed path; None = install-only
    required: bool = True  # missing => specialty incomplete => degraded


@dataclass(frozen=True, slots=True)
class SpecialtyKind:
    """One specialty distribution the platform understands."""

    key: str  # doubles as the RunnerSupports.specialties token
    quant_marker: str | None  # matched against quant / model filename
    companions: tuple[CompanionSpec, ...]
    mode_env: Mapping[str, str] = field(default_factory=dict)
    degraded_ok: bool = True  # is a plain-GGUF fallback legitimate?
    default_ctx: int | None = None
    argv_profile: str | None = None  # seed profile carrying the card argv


SPECIALTY_KINDS: dict[str, SpecialtyKind] = {
    "promptforge": SpecialtyKind(
        key="promptforge",
        quant_marker="ActiveFPX",
        companions=(
            CompanionSpec(
                role="promptforge_ffn",
                pattern=re.compile(r"ffn[^/]*\.pfs$", re.IGNORECASE),
                env="PROMPTFORGE_SIDECAR",
            ),
            CompanionSpec(
                role="promptforge_gdn",
                pattern=re.compile(r"gdn[^/]*\.pfs$", re.IGNORECASE),
                env="PROMPTFORGE_GDN_SIDECAR",
            ),
            CompanionSpec(
                role="promptforge_output_k8",
                pattern=re.compile(r"output[-_]?k8[^/]*\.pfs$", re.IGNORECASE),
                env="PROMPTFORGE_MTP_OUTPUT_K8_PROXY",
            ),
            CompanionSpec(
                role="runtime_patch",
                pattern=re.compile(r"[^/]*runtime\.patch$", re.IGNORECASE),
                env=None,  # consumed by the image build; kept for provenance
                required=False,
            ),
        ),
        # Card's mode envs ride here once validated on ct150; empty until then.
        mode_env={},
        degraded_ok=True,
        default_ctx=262_144,
        argv_profile="promptforge",
    ),
}


def specialty_env_for(metadata: Mapping[str, object]) -> dict[str, str]:
    """Synthesize the env block for a specialty model's accelerated launch.

    ``{CompanionSpec.env: installed path}`` for every companion present in
    ``metadata["companions"]``, plus the kind's static ``mode_env``.
    Empty dict for plain models, unknown kinds, or missing companions —
    the caller gates completeness via the guard, this never raises.
    """
    key = metadata.get("specialty")
    kind = SPECIALTY_KINDS.get(key) if isinstance(key, str) else None
    companions = metadata.get("companions")
    if kind is None or not isinstance(companions, Mapping) or not companions:
        return {}
    env: dict[str, str] = {}
    for spec in kind.companions:
        if spec.env is None:
            continue
        path = companions.get(spec.role)
        if isinstance(path, str) and path:
            env[spec.env] = path
    env.update(kind.mode_env)
    return env


def companion_role_of(filename: str) -> str | None:
    """Classify one filename into a companion role, or ``None``.

    First match across all kinds wins; patterns are anchored on the
    basename so a repo path or bare name classify identically.
    """
    name = PurePosixPath(filename).name
    for kind in SPECIALTY_KINDS.values():
        for spec in kind.companions:
            if spec.pattern.search(name):
                return spec.role
    return None


def kind_for_role(role: str) -> SpecialtyKind | None:
    """Map a companion role back to its owning kind."""
    for kind in SPECIALTY_KINDS.values():
        if any(spec.role == role for spec in kind.companions):
            return kind
    return None


def detect_specialty(paths: Iterable[str], quant: str | None = None) -> str | None:
    """Detect which specialty kind (if any) a file listing belongs to.

    Two independent signals, either suffices:
    1. the kind's ``quant_marker`` appears in ``quant`` or any filename;
    2. any *required* companion pattern matches a file.
    Never guesses: no signal => ``None``.
    """
    names = [PurePosixPath(p).name for p in paths]
    for kind in SPECIALTY_KINDS.values():
        marker = kind.quant_marker
        if marker and (
            (quant and marker.lower() in quant.lower())
            or any(marker.lower() in n.lower() for n in names)
        ):
            return kind.key
        for spec in kind.companions:
            if spec.required and any(spec.pattern.search(n) for n in names):
                return kind.key
    return None
