"""#1199: orchestrate the curated ComfyUI model set in one command.

Operators previously had to know the right invocation sequence and per-variant
argv for each of the individual ``get_*.sh`` scripts (SDXL, Qwen Image, Wan 2.2,
LTX-2, Hunyuan 1.5, ESRGAN). This module drives the curated *default* variant of
every :data:`~hal0.comfyui.capabilities.CAPABILITIES` entry end-to-end, running
each script's ``fetch_steps`` in sequence, logging every step's start and exit
code, and writing a single operator-readable log.

Discipline mirrors the rest of the ComfyUI provisioning stack:

* A failed **optional** asset (ESRGAN's ``4x-UltraSharp`` mirror, #1200) never
  blocks the unrelated model families — the sequence continues and the run only
  reports non-``ok`` when a **required** family fails.
* The underlying scripts skip already-present destination files, so the whole
  command is safe to re-run (idempotent).

The heavy lifting (subprocess spawn, HF-credential env, script dir) is shared
with :mod:`hal0.comfyui.fetch`; the ``runner``/``clock``/``log_dir`` seams are
injectable so the orchestration loop is unit-testable without real downloads.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from hal0.comfyui.capabilities import CAPABILITIES, ModelVariant, default_variant
from hal0.comfyui.fetch import _SCRIPTS_DIR, _fetch_env, _provision_workflow

log = logging.getLogger(__name__)

#: Families whose absence must NOT fail the curated run (optional assets, #1200).
OPTIONAL_FAMILIES: frozenset[str] = frozenset({"esrgan"})

_TAG = "[comfyui-orchestrate]"


@dataclass
class FamilyResult:
    """Per-family outcome of an orchestration run."""

    capability: str
    family: str
    script: str
    steps: int
    ok: bool
    optional: bool
    #: exit code of the first failing step, or ``None`` when every step exited 0.
    returncode: int | None = None
    #: 1-indexed step that failed, or ``None`` on success.
    failed_step: int | None = None


@dataclass
class OrchestrationResult:
    """Summary of a curated-model orchestration run."""

    results: list[FamilyResult] = field(default_factory=list)
    log_path: str | None = None

    @property
    def landed(self) -> list[str]:
        return [r.family for r in self.results if r.ok]

    @property
    def failed_required(self) -> list[str]:
        return [r.family for r in self.results if not r.ok and not r.optional]

    @property
    def failed_optional(self) -> list[str]:
        return [r.family for r in self.results if not r.ok and r.optional]

    @property
    def ok(self) -> bool:
        """True when no *required* family failed (optional failures tolerated)."""
        return not self.failed_required


def curated_set() -> list[tuple[str, ModelVariant]]:
    """Curated ``(capability_id, default variant)`` pairs, in CAPABILITIES order."""
    return [(cap_id, default_variant(cap)) for cap_id, cap in CAPABILITIES.items()]


def _default_runner(cmd: list[str], env: dict[str, str], log_fh: TextIO) -> int:
    """Run *cmd*, streaming combined stdout/stderr into *log_fh*; return exit code."""
    import subprocess

    log_fh.flush()
    proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT, env=env)
    return proc.wait()


def _default_log_path(log_dir: Path | None, clock: Callable[[], float]) -> Path:
    """Resolve the orchestration log path, honouring the model-store layout."""
    if log_dir is None:
        try:
            from hal0.config.paths import model_store_root

            log_dir = Path(model_store_root()) / "comfyui" / "logs"
        except Exception:
            log_dir = Path("/tmp/hal0-comfyui-logs")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(clock()))
    return Path(log_dir) / f"orchestrate-{stamp}.log"


def orchestrate_models(
    pairs: list[tuple[str, ModelVariant]] | None = None,
    *,
    scripts_dir: Path = _SCRIPTS_DIR,
    env_fn: Callable[[], dict[str, str]] = _fetch_env,
    provision_workflow: Callable[[ModelVariant], str | None] = _provision_workflow,
    runner: Callable[[list[str], dict[str, str], TextIO], int] = _default_runner,
    log_dir: Path | None = None,
    log_path: Path | None = None,
    clock: Callable[[], float] = time.time,
    on_line: Callable[[str], None] | None = None,
) -> OrchestrationResult:
    """Run the curated ComfyUI model pull sequence end-to-end, writing a log.

    Each variant's ``fetch_steps`` are run in order; the first non-zero step marks
    that family failed and moves on to the next family (so unrelated families
    still download). Optional families (:data:`OPTIONAL_FAMILIES`) never affect
    :attr:`OrchestrationResult.ok`. The variant's curated workflow JSON is
    provisioned before its download, matching :func:`hal0.comfyui.fetch.fetch_model`.

    Dependencies (``runner``/``env_fn``/``clock``/``log_dir``) are injected so the
    loop runs deterministically under test without real subprocesses.
    """
    if pairs is None:
        pairs = curated_set()

    resolved_log = Path(log_path) if log_path is not None else _default_log_path(log_dir, clock)
    resolved_log.parent.mkdir(parents=True, exist_ok=True)

    result = OrchestrationResult(log_path=str(resolved_log))
    env = env_fn()

    def emit(line: str, fh: TextIO) -> None:
        fh.write(line + "\n")
        fh.flush()
        if on_line is not None:
            on_line(line)

    with resolved_log.open("w", encoding="utf-8") as fh:
        emit(f"{_TAG} curated ComfyUI model set — {len(pairs)} families", fh)
        for cap_id, variant in pairs:
            optional = variant.family in OPTIONAL_FAMILIES
            tag = "optional" if optional else "required"
            emit(f"{_TAG} === {variant.family} ({cap_id}, {tag}) ===", fh)

            # Provision the curated workflow JSON (best-effort; a copy hiccup must
            # not fail the download) before pulling weights.
            try:
                wf = provision_workflow(variant)
                if wf:
                    emit(f"{_TAG} provisioned workflow → {wf}", fh)
            except Exception as exc:  # pragma: no cover - defensive
                emit(f"{_TAG} workflow provision skipped: {exc}", fh)

            script_path = str(scripts_dir / variant.fetch_script)
            steps = variant.fetch_steps or ((),)
            fam_ok = True
            fam_rc: int | None = None
            failed_step: int | None = None

            for idx, step_args in enumerate(steps, 1):
                cmd = ["bash", script_path, *step_args]
                emit(f"{_TAG} step {idx}/{len(steps)}: {' '.join(cmd)}", fh)
                rc = runner(cmd, env, fh)
                emit(f"{_TAG} {variant.family} step {idx} exit={rc}", fh)
                if rc != 0:
                    fam_ok = False
                    fam_rc = rc
                    failed_step = idx
                    break

            if fam_ok:
                emit(f"{_TAG} {variant.family}: OK", fh)
            else:
                emit(
                    f"{_TAG} {variant.family}: FAILED at step {failed_step} "
                    f"(exit={fam_rc}){' [optional — skipped]' if optional else ''}",
                    fh,
                )

            result.results.append(
                FamilyResult(
                    capability=cap_id,
                    family=variant.family,
                    script=variant.fetch_script,
                    steps=len(steps),
                    ok=fam_ok,
                    optional=optional,
                    returncode=fam_rc,
                    failed_step=failed_step,
                )
            )

        emit(
            f"{_TAG} summary: landed={result.landed} "
            f"failed_required={result.failed_required} "
            f"failed_optional={result.failed_optional}",
            fh,
        )
        emit(f"{_TAG} log written to: {resolved_log}", fh)

    return result


__all__ = [
    "OPTIONAL_FAMILIES",
    "FamilyResult",
    "OrchestrationResult",
    "curated_set",
    "orchestrate_models",
]
