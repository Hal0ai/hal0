"""Tests for the Hermes WebUI (hermex) companion provisioning module."""
import subprocess
from pathlib import Path

import hal0.agents.hermes_webui as hw


def test_pinned_ref_is_vetted() -> None:
    assert hw.WEBUI_PINNED_REF in hw.VETTED_HERMES_WEBUI_REFS
    assert len(hw.WEBUI_PINNED_REF) == 40  # full SHA, not a short ref


def test_ensure_env_seeds_all_defaults_on_fresh_tree(tmp_path: Path) -> None:
    tree = tmp_path / "webui"
    tree.mkdir()
    venv = tmp_path / "venv"
    out = hw.ensure_env(tree, venv)
    assert out.ok and out.changed
    body = (tree / ".env").read_text(encoding="utf-8")
    assert "HERMES_WEBUI_HOST=127.0.0.1" in body
    assert "HERMES_WEBUI_PORT=8787" in body
    assert f"HERMES_WEBUI_PYTHON={venv}/bin/python3" in body
    # a password was generated and is non-trivial
    pw_line = next(
        line for line in body.splitlines() if line.startswith("HERMES_WEBUI_PASSWORD=")
    )
    assert len(pw_line.split("=", 1)[1]) >= 24
    assert ((tree / ".env").stat().st_mode & 0o777) == 0o600


def test_ensure_env_preserves_operator_edits(tmp_path: Path) -> None:
    tree = tmp_path / "webui"
    tree.mkdir()
    existing = "# operator file\nHERMES_WEBUI_HOST=0.0.0.0\nHERMES_WEBUI_PASSWORD=operator-secret\n"
    (tree / ".env").write_text(existing, encoding="utf-8")
    out = hw.ensure_env(tree, tmp_path / "venv")
    body = (tree / ".env").read_text(encoding="utf-8")
    assert out.changed  # PORT + PYTHON were appended
    assert "HERMES_WEBUI_HOST=0.0.0.0" in body           # untouched
    assert "HERMES_WEBUI_PASSWORD=operator-secret" in body  # untouched
    assert body.startswith("# operator file")             # comments preserved
    assert "HERMES_WEBUI_PORT=8787" in body


def test_ensure_env_converges_second_run(tmp_path: Path) -> None:
    tree = tmp_path / "webui"
    tree.mkdir()
    hw.ensure_env(tree, tmp_path / "venv")
    second = hw.ensure_env(tree, tmp_path / "venv")
    assert second.ok and not second.changed


class _FakeRun:
    """Scriptable subprocess.run stand-in keyed on the git subcommand."""

    def __init__(self, responses: dict[str, tuple[int, str, str]]):
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        key = next((a for a in ("rev-parse", "status", "fetch", "checkout", "clone") if a in argv), argv[0])
        code, out, err = self.responses.get(key, (0, "", ""))
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr=err)


def _git_tree(tmp_path: Path) -> Path:
    tree = tmp_path / "webui"
    (tree / ".git").mkdir(parents=True)
    return tree


def test_ensure_tree_noop_when_at_pinned_ref(tmp_path: Path) -> None:
    tree = _git_tree(tmp_path)
    run = _FakeRun({"rev-parse": (0, hw.WEBUI_PINNED_REF + "\n", "")})
    out = hw.ensure_tree(tree, run=run)
    assert out.ok and not out.changed
    assert not any("fetch" in c for c in run.calls)


def test_ensure_tree_refuses_dirty_tree(tmp_path: Path) -> None:
    tree = _git_tree(tmp_path)
    run = _FakeRun({
        "rev-parse": (0, "0" * 40 + "\n", ""),
        "status": (0, " M server.py\n", ""),
    })
    out = hw.ensure_tree(tree, run=run)
    assert not out.ok and not out.changed
    assert "dirty" in out.detail


def test_ensure_tree_moves_clean_tree_to_pin(tmp_path: Path) -> None:
    tree = _git_tree(tmp_path)
    run = _FakeRun({
        "rev-parse": (0, "0" * 40 + "\n", ""),
        "status": (0, "", ""),
        "fetch": (0, "", ""),
        "checkout": (0, "", ""),
    })
    out = hw.ensure_tree(tree, run=run)
    assert out.ok and out.changed


def test_ensure_tree_refuses_unmanaged_nonempty_dir(tmp_path: Path) -> None:
    tree = tmp_path / "webui"
    tree.mkdir()
    (tree / "junk.txt").write_text("x")
    out = hw.ensure_tree(tree, run=_FakeRun({}))
    assert not out.ok
    assert "unmanaged" in out.detail


def test_ensure_tree_clones_when_absent(tmp_path: Path) -> None:
    tree = tmp_path / "webui"
    run = _FakeRun({"clone": (0, "", ""), "checkout": (0, "", "")})
    out = hw.ensure_tree(tree, run=run)
    assert out.ok and out.changed
    assert any("clone" in c for c in run.calls)


def test_ensure_tree_rejects_unvetted_ref(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HAL0_ALLOW_UNVETTED_HERMES_WEBUI", raising=False)
    out = hw.ensure_tree(tmp_path / "webui", ref="f" * 40, run=_FakeRun({}))
    assert not out.ok
    assert "unvetted" in out.detail
