"""Every registry lookup threads the slot's effective provider.

The staged-weight providers (Qwen3-TTS, Moonshine) ship no registry row, so
``_resolve_model_info`` must know WHICH provider a lookup belongs to before it
can decide a miss is expected (``provider_registry_miss_is_expected``,
``src/hal0/slots/state.py``). A call site that drops ``provider=`` turns every
expected miss back into a ``slot.model_not_in_registry`` warning — the #3408
regression — and nothing else in the tree notices: the lookup still succeeds,
the slot still starts, only the log turns noisy again.

So this module pins the CALL SITES, which is the regression surface: every
invocation of ``_resolve_model_info`` under ``src/hal0/slots/`` must pass a
``provider=`` keyword derived from the slot's config. The warning semantics
live in ``tests/slots/test_registry_lookup_warnings.py``; this is the wiring
that feeds them.

Pinning is by (module, enclosing function), not by line number, so ordinary
edits inside a function do not churn the list.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: The slot tree under test, resolved from the repo checkout rather than from
#: the imported package — an installed (non-editable) hal0 would point at a
#: site-packages copy, and this scan must read the working tree.
_SLOTS_DIR = Path(__file__).resolve().parents[2] / "src" / "hal0" / "slots"

#: The resolver every slot-tree lookup goes through.
_RESOLVER = "_resolve_model_info"

#: The call sites the fix threads a provider through, as
#: ``(module, enclosing function)``, where ``module`` is the path relative to
#: the slot tree (``capacity.py``, ``npu/trio.py``) so two modules sharing a
#: basename stay distinct. A new lookup site must be added here with its
#: provider threading; a removed one must disappear.
_PINNED_CALL_SITES: frozenset[tuple[str, str]] = frozenset(
    {
        ("capacity.py", "halogen_footprint_readout"),
        ("drift.py", "compute_config_drift"),
        ("interface.py", "inspect"),
        ("manager.py", "_load"),
        ("manager.py", "_spawn_locked"),
        ("manager.py", "_check_swap"),
        ("preload_evict.py", "_estimate_incoming_footprint_mb"),
        ("routing.py", "_model_info_for"),
    }
)


def _resolver_aliases(tree: ast.Module) -> set[str]:
    """Local names bound to the resolver via ``getattr(host, "_resolve_model_info")``.

    ``routing._model_info_for`` resolves the host's method dynamically, so its
    call reads ``resolver(...)`` — a plain name, not ``_resolve_model_info(...)``.
    Without this the scan would miss that call site entirely.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if not (isinstance(func, ast.Name) and func.id == "getattr"):
            continue
        args = node.value.args
        if len(args) < 2:
            continue
        attr = args[1]
        if not (isinstance(attr, ast.Constant) and attr.value == _RESOLVER):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                aliases.add(target.id)
    return aliases


def _scan_tree(tree: ast.Module, aliases: set[str]) -> list[tuple[str, int, ast.Call]]:
    """Every resolver call in one module, with its enclosing function name."""
    sites: list[tuple[str, int, ast.Call]] = []

    def visit(node: ast.AST, func: str) -> None:
        here = func
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            here = node.name
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                callee = child.func
                is_attr = isinstance(callee, ast.Attribute) and callee.attr == _RESOLVER
                is_alias = isinstance(callee, ast.Name) and callee.id in aliases
                if is_attr or is_alias:
                    sites.append((here, child.lineno, child))
            visit(child, here)

    visit(tree, "<module>")
    return sites


def _scan() -> list[tuple[str, str, int, ast.Call]]:
    """Every resolver call in the slot tree: (module, function, line, call)."""
    sites: list[tuple[str, str, int, ast.Call]] = []
    for path in sorted(_SLOTS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _resolver_aliases(tree)
        for func, lineno, call in _scan_tree(tree, aliases):
            # Key by the slot-tree-relative path, not the basename: two modules
            # can share a basename (``trio.py`` and ``npu/trio.py``), and the
            # basename would collapse them into one pinned entry.
            module = path.relative_to(_SLOTS_DIR).as_posix()
            sites.append((module, func, lineno, call))
    return sites


def _keyword_names(call: ast.Call) -> set[str]:
    return {kw.arg for kw in call.keywords if kw.arg is not None}


def test_scan_finds_unthreaded_lookup_in_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nested slot module must be included in the provider-threading guard."""
    nested = tmp_path / "npu"
    nested.mkdir()
    (nested / "trio.py").write_text(
        "async def nested_lookup(host, model_id):\n    await host._resolve_model_info(model_id)\n",
        encoding="utf-8",
    )
    module = __import__(__name__, fromlist=["_SLOTS_DIR"])
    monkeypatch.setattr(module, "_SLOTS_DIR", tmp_path)

    unthreaded = [
        (name, func) for name, func, _, call in _scan() if "provider" not in _keyword_names(call)
    ]

    assert unthreaded == [("npu/trio.py", "nested_lookup")]


def test_scan_keeps_same_basename_siblings_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two slot modules sharing a basename must not collapse into one entry.

    Keying the scan by basename would merge ``trio.py`` and ``npu/trio.py``
    into one pinned pair, so dropping the provider from one of them would
    leave the pinned set unchanged and the drift check green.
    """
    nested = tmp_path / "npu"
    nested.mkdir()
    (tmp_path / "trio.py").write_text(
        "async def lookup(host, model_id):\n"
        "    await host._resolve_model_info(model_id, provider=host.effective_provider)\n",
        encoding="utf-8",
    )
    (nested / "trio.py").write_text(
        "async def lookup(host, model_id):\n"
        "    await host._resolve_model_info(model_id, provider=host.effective_provider)\n",
        encoding="utf-8",
    )
    module = __import__(__name__, fromlist=["_SLOTS_DIR"])
    monkeypatch.setattr(module, "_SLOTS_DIR", tmp_path)

    found = {(name, func) for name, func, _, _ in _scan()}

    assert found == {("trio.py", "lookup"), ("npu/trio.py", "lookup")}


def _provider_value(call: ast.Call) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == "provider":
            return kw.value
    return None


def test_every_registry_lookup_call_site_threads_the_provider() -> None:
    """No lookup may omit the provider — an omitted one re-arms #3408."""
    unthreaded = [
        f"{name}:{func}:{lineno}"
        for name, func, lineno, call in _scan()
        if "provider" not in _keyword_names(call)
    ]
    assert not unthreaded, (
        "registry lookups without provider= (the provider decides whether a "
        f"registry miss is expected): {unthreaded}"
    )


def test_the_pinned_call_sites_are_the_known_ones() -> None:
    """The scan finds exactly the call sites the fix threads — no drift, no dupes."""
    found = {(name, func) for name, func, _, _ in _scan()}
    assert found == set(_PINNED_CALL_SITES), (
        "the pinned registry-lookup call sites changed "
        f"(missing: {sorted(set(_PINNED_CALL_SITES) - found)}, "
        f"unexpected: {sorted(found - set(_PINNED_CALL_SITES))}); re-check each "
        "one threads provider="
    )


def test_a_provider_keyword_is_never_a_bare_none() -> None:
    """``provider=None`` is as unthreaded as omitting it.

    ``provider_registry_miss_is_expected(None)`` is False, so a bare ``None``
    re-arms the warning exactly like a missing keyword.
    """
    bare_none = [
        f"{name}:{func}:{lineno}"
        for name, func, lineno, call in _scan()
        if isinstance((value := _provider_value(call)), ast.Constant) and value.value is None
    ]
    assert not bare_none, f"registry lookups with a bare provider=None: {bare_none}"
