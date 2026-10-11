"""Runner-image pins: the ``runners`` table in the repo-root ``manifest.json``.

That table is the one place a runner image is named (ADR-0028). It maps each
runner key in :data:`hal0.runners.RUNNER_IMAGES` to a registry repo, an
immutable tag, a content digest and the backends the hardware gate passed for
that digest. Everything else that needs an image ref derives it from here:

* :attr:`hal0.runners.Runner.image` (the ref a slot launches with no pin),
* :func:`vulkan_capable_refs` (the images allowed to serve the gpu-vulkan lane),
* ``exports/runner-image-pins.json`` (the retention allowlist the
  runner-images repo fetches; see :func:`export_payload`),
* the bench harness lanes, ``hal0 doctor toolbox-pull``, and the installer's
  Stage-1 preflight (``installer/lib/preflight.sh`` reads the same JSON).

``tag: null`` marks a runner whose image is not published yet: it has no ref
(:attr:`RunnerPin.ref` raises), so nothing can launch it unless a
``HAL0_RUNNER_IMAGE_<KEY>`` override names an image. A bare repo is never used:
podman would pull whatever ``latest`` is. ``digest: null`` pulls by tag.
:func:`release_gate_problems` lists the default-tier runners still in either
state; ``scripts/release-check.sh`` refuses to cut a release while it is
non-empty (``scripts/check_runner_pins.py``).
"""

from __future__ import annotations

import functools
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hal0.errors import Hal0Error

#: The env override prefix: ``HAL0_RUNNER_IMAGE_<KEY>`` replaces a runner's
#: pinned ref outright (an operator's own build, a rollback).
ENV_OVERRIDE_PREFIX = "HAL0_RUNNER_IMAGE_"

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
#: ``b<N>`` llama.cpp release number inside a tag (``b11552-r1a2b3c4``,
#: ``server-cuda-b11552``).
_LLAMA_RELEASE_RE = re.compile(r"(?:^|-)b(\d+)(?:-|$)")


class RunnerPinsError(Hal0Error):
    """The ``runners`` pin table is missing or malformed."""

    code = "runners.pins_invalid"
    status = 500


@dataclass(frozen=True, slots=True)
class RunnerPin:
    """One row of the pin table."""

    key: str
    image: str
    tag: str | None = None
    digest: str | None = None
    lanes_gated: tuple[str, ...] = ()

    @property
    def published(self) -> bool:
        """True once the row names a tag (``tag: null`` = not built yet)."""
        return self.tag is not None

    @property
    def ref(self) -> str:
        """The pull-ready ref: ``repo:tag@digest`` or ``repo:tag``.

        Raises :class:`RunnerPinsError` for an unpublished row (``tag: null``):
        the bare repo would pull ``latest``, never a pinned image.
        """
        if self.tag and self.digest:
            return f"{self.image}:{self.tag}@{self.digest}"
        if self.tag:
            return f"{self.image}:{self.tag}"
        raise RunnerPinsError(
            f"runner {self.key!r} is not published yet (manifest.json has no tag for "
            f"it); set {ENV_OVERRIDE_PREFIX}{self.key.upper()} to run an image of your own",
            details={"runner": self.key},
        )

    def spellings(self) -> frozenset[str]:
        """Every ref spelling that names exactly this pinned image.

        A slot or config may carry the pin as ``repo:tag@digest``,
        ``repo:tag`` or ``repo@digest``; all three name the same bytes. The
        bare repo is never included (it names whatever ``latest`` is).
        """
        if not self.published:
            return frozenset()
        out = {self.ref}
        if self.tag:
            out.add(f"{self.image}:{self.tag}")
        if self.digest:
            out.add(f"{self.image}@{self.digest}")
        return frozenset(out)

    def matches(self, ref: str | None) -> bool:
        """Does ``ref`` name this pinned image?"""
        return bool(ref) and (ref or "").strip() in self.spellings()


def manifest_path() -> Path | None:
    """The ``manifest.json`` that ships with the running code.

    Checked in this order, first hit wins:

    1. the repo root next to this source file (a checkout or editable
       install, tests included: the pins must match the code being run);
    2. the installed release tree (``/usr/lib/hal0/current/manifest.json``),
       which carries the manifest of the release the venv was built from;
    3. :func:`hal0.config.paths.manifest_json` (the FHS ``/etc`` copy).
    """
    repo_root = Path(__file__).resolve().parents[3] / "manifest.json"
    if repo_root.is_file():
        return repo_root
    from hal0.config import paths

    for candidate in (paths.usr_lib() / "manifest.json", paths.manifest_json()):
        if candidate.is_file():
            return candidate
    return None


def _str_or_none(value: Any, field: str, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RunnerPinsError(
            f"manifest.json runners.{key}.{field} must be a non-empty string or null",
            details={"runner": key, "field": field},
        )
    return value.strip()


def parse_runner_pins(manifest: Mapping[str, Any]) -> dict[str, RunnerPin]:
    """Validate and parse ``manifest["runners"]``. Raises :class:`RunnerPinsError`."""
    table = manifest.get("runners")
    if not isinstance(table, Mapping) or not table:
        raise RunnerPinsError("manifest.json has no `runners` pin table")
    pins: dict[str, RunnerPin] = {}
    for key, row in table.items():
        if not isinstance(row, Mapping):
            raise RunnerPinsError(
                f"manifest.json runners.{key} must be an object", details={"runner": key}
            )
        image = _str_or_none(row.get("image"), "image", key)
        if image is None:
            raise RunnerPinsError(
                f"manifest.json runners.{key}.image is required", details={"runner": key}
            )
        if ":" in image.rsplit("/", 1)[-1] or "@" in image:
            raise RunnerPinsError(
                f"manifest.json runners.{key}.image must be a bare repo (no tag or digest)",
                details={"runner": key, "image": image},
            )
        tag = _str_or_none(row.get("tag"), "tag", key)
        digest = _str_or_none(row.get("digest"), "digest", key)
        if digest is not None and not _DIGEST_RE.match(digest):
            raise RunnerPinsError(
                f"manifest.json runners.{key}.digest must be sha256:<64 hex>",
                details={"runner": key, "digest": digest},
            )
        if digest is not None and tag is None:
            raise RunnerPinsError(
                f"manifest.json runners.{key} has a digest but no tag",
                details={"runner": key},
            )
        lanes = row.get("lanes_gated", [])
        if not isinstance(lanes, list) or not all(isinstance(x, str) for x in lanes):
            raise RunnerPinsError(
                f"manifest.json runners.{key}.lanes_gated must be a list of backends",
                details={"runner": key},
            )
        pins[str(key)] = RunnerPin(
            key=str(key),
            image=image,
            tag=tag,
            digest=digest,
            lanes_gated=tuple(lanes),
        )
    return pins


def load_runner_pins(path: Path | None = None) -> dict[str, RunnerPin]:
    """Read and parse the pin table from ``path`` (default: :func:`manifest_path`)."""
    resolved = path if path is not None else manifest_path()
    if resolved is None:
        raise RunnerPinsError("manifest.json not found; the runner pin table is unavailable")
    try:
        manifest = json.loads(Path(resolved).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunnerPinsError(
            f"cannot read {resolved}: {exc}", details={"path": str(resolved)}
        ) from exc
    if not isinstance(manifest, Mapping):
        raise RunnerPinsError(f"{resolved} is not a JSON object")
    return parse_runner_pins(manifest)


@functools.cache
def runner_pins() -> dict[str, RunnerPin]:
    """The pin table for the running code, read once per process."""
    return load_runner_pins()


def pin_for(key: str) -> RunnerPin:
    """The pin row for ``key``. Raises :class:`RunnerPinsError` when absent."""
    pin = runner_pins().get(key)
    if pin is None:
        raise RunnerPinsError(
            f"manifest.json runners table has no row for runner {key!r}",
            details={"runner": key},
        )
    return pin


def env_override(key: str) -> str | None:
    """``HAL0_RUNNER_IMAGE_<KEY>`` when set and non-blank."""
    value = os.environ.get(f"{ENV_OVERRIDE_PREFIX}{key.upper()}", "").strip()
    return value or None


def vulkan_capable_refs() -> frozenset[str]:
    """Image refs allowed to serve the gpu-vulkan llama.cpp lane.

    A runner's pinned image qualifies when the runner can run on Vulkan
    (``"vulkan"`` in :attr:`hal0.runners.Runner.supported_backends`) AND the
    hardware gate passed the Vulkan lane for that exact digest
    (``"vulkan"`` in the row's ``lanes_gated``). Admission is earned per
    digest, as data next to the digest, never by a hand-kept list.
    """
    from hal0.runners import RUNNER_IMAGES

    refs: set[str] = set()
    for key, runner in RUNNER_IMAGES.items():
        if "vulkan" not in runner.supported_backends:
            continue
        pin = runner_pins().get(key)
        if pin is not None and "vulkan" in pin.lanes_gated:
            refs |= pin.spellings()
    return frozenset(refs)


def llama_release(tag: str | None) -> int | None:
    """The llama.cpp release number a tag names (``b11552`` → 11552), if any."""
    if not tag:
        return None
    match = _LLAMA_RELEASE_RE.search(tag)
    return int(match.group(1)) if match else None


#: How far the ``cuda`` pin may trail ``standard`` before the release gate
#: warns. ggml-org publishes ``server-cuda-b<N>`` for some releases only, so
#: the CUDA pin is the newest one at or below standard's release.
CUDA_RELEASE_LAG_WARN = 50


def _cuda_release_checks(
    table: Mapping[str, RunnerPin],
) -> tuple[list[str], list[str]]:
    """``(problems, warnings)`` for the ``cuda`` pin against ``standard``'s release."""
    cuda = table.get("cuda")
    if cuda is None or cuda.tag is None:
        return [], []  # optional tier: an unpublished cuda pin never blocks
    if not cuda.tag.startswith("server-cuda-b") or llama_release(cuda.tag) is None:
        return [f"cuda tag {cuda.tag!r} is not a server-cuda-b<N> release tag"], []
    standard = table.get("standard")
    std_release = llama_release(standard.tag) if standard is not None else None
    cuda_release = llama_release(cuda.tag)
    assert cuda_release is not None
    if std_release is None:
        return [], []
    if cuda_release > std_release:
        return [f"cuda pins b{cuda_release}, newer than standard's b{std_release}"], []
    if std_release - cuda_release > CUDA_RELEASE_LAG_WARN:
        return [], [
            f"cuda pins b{cuda_release}, {std_release - cuda_release} releases behind "
            f"standard's b{std_release}"
        ]
    return [], []


def release_gate_problems(pins: Mapping[str, RunnerPin] | None = None) -> list[str]:
    """Reasons this pin table cannot ship in a release; ``[]`` when it can.

    * every runner in :data:`hal0.runners.RUNNER_IMAGES` has a row, and every
      row names a registered runner;
    * every ``default``-tier runner names a tag AND a digest (``cuda`` and
      ``qwen3tts`` are optional and may stay unpublished);
    * ``standard`` and ``cpu`` name the same llama.cpp ``b<N>`` release;
    * a published ``cuda`` pin is a ``server-cuda-b<M>`` tag with ``M <= N``.
    """
    from hal0.runners import RUNNER_IMAGES

    table = dict(pins) if pins is not None else runner_pins()
    problems: list[str] = []
    for key in RUNNER_IMAGES:
        if key not in table:
            problems.append(f"runner {key!r} has no row in manifest.json runners")
    for key in table:
        if key not in RUNNER_IMAGES:
            problems.append(f"manifest.json runners.{key} names no registered runner")
    for key, runner in RUNNER_IMAGES.items():
        pin = table.get(key)
        if pin is None or runner.tier != "default":
            continue
        if pin.tag is None:
            problems.append(f"default runner {key!r} has no published tag (tag is null)")
        if pin.digest is None:
            problems.append(f"default runner {key!r} is not digest-pinned (digest is null)")
    releases = {
        key: llama_release(table[key].tag)
        for key in ("standard", "cpu")
        if key in table and table[key].tag is not None
    }
    if len(releases) == 2 and (None in releases.values() or len(set(releases.values())) > 1):
        problems.append(
            "standard and cpu must name one llama.cpp release: "
            + ", ".join(f"{k}={table[k].tag}" for k in sorted(releases))
        )
    problems += _cuda_release_checks(table)[0]
    return problems


def release_gate_warnings(pins: Mapping[str, RunnerPin] | None = None) -> list[str]:
    """Non-blocking notes for the release ritual (a lagging ``cuda`` pin)."""
    table = dict(pins) if pins is not None else runner_pins()
    return _cuda_release_checks(table)[1]


def export_payload(pins: Mapping[str, RunnerPin] | None = None) -> dict[str, Any]:
    """The ``exports/runner-image-pins.json`` body: every published pinned ref.

    The runner-images retention automation fetches that file from ``main`` as
    its never-delete allowlist, so it lists each published row's full ref.
    """
    table = dict(pins) if pins is not None else runner_pins()
    refs = sorted(pin.ref for pin in table.values() if pin.published)
    return {"pins": refs, "source": "manifest.json"}


__all__ = [
    "ENV_OVERRIDE_PREFIX",
    "RunnerPin",
    "RunnerPinsError",
    "env_override",
    "export_payload",
    "llama_release",
    "load_runner_pins",
    "manifest_path",
    "parse_runner_pins",
    "pin_for",
    "release_gate_problems",
    "release_gate_warnings",
    "runner_pins",
    "vulkan_capable_refs",
]
