"""Layering guard for :mod:`hal0.diagnostics` (spec-21-4-doctor.md §7 risk #6).

``hal0.diagnostics`` sits BELOW the CLI layer on purpose: a future
non-CLI probe (e.g. §21.2's gfx-arch guard, which lives under
``hal0.runners``/``hal0.slots``) needs to emit a ``Diagnosis`` without
pulling in Typer/Rich or creating an import cycle back into ``hal0.cli``.
This test statically enforces that direction so a future edit can't
casually add a ``from hal0.cli import ...`` to the module.
"""

from __future__ import annotations

import ast
from pathlib import Path

import hal0.diagnostics as diagnostics_mod


def test_diagnostics_module_does_not_import_cli() -> None:
    src = Path(diagnostics_mod.__file__).read_text()
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    offenders = {m for m in imported if m == "hal0.cli" or m.startswith("hal0.cli.")}
    assert not offenders, f"hal0.diagnostics must not import hal0.cli: {offenders}"


def test_diagnostics_module_is_pure_stdlib_plus_typing() -> None:
    """No third-party (rich/typer/fastapi/...) imports either — a leaf module."""
    src = Path(diagnostics_mod.__file__).read_text()
    tree = ast.parse(src)
    top_level_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            top_level_modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level_modules.add(node.module.split(".")[0])
    allowed = {"__future__", "dataclasses", "typing"}
    offenders = top_level_modules - allowed
    assert not offenders, f"unexpected non-stdlib import in hal0.diagnostics: {offenders}"
