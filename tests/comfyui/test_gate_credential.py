"""The ComfyUI GPU gate's own credential (#3058): mint, read, verify."""

from __future__ import annotations

import os
import stat

import pytest

from hal0.comfyui import gate_credential


def test_ensure_token_mints_a_private_token_once() -> None:
    token = gate_credential.ensure_token()

    assert len(token) == 43
    assert gate_credential.ensure_token() == token  # minted once, then read back
    assert gate_credential.read_token() == token
    assert stat.S_IMODE(os.stat(gate_credential.token_dir()).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(gate_credential.token_path()).st_mode) == 0o600


def test_the_token_lives_outside_the_comfyui_tree() -> None:
    """The container mounts parts of the ComfyUI data root read-write; the
    token directory is none of them."""
    from hal0.comfyui import paths as comfyui_paths

    assert comfyui_paths.data_root() not in gate_credential.token_dir().parents
    assert gate_credential.token_dir() != comfyui_paths.data_root()


def test_no_token_file_means_no_credential() -> None:
    assert gate_credential.read_token() is None
    assert gate_credential.credential_present() is False
    assert gate_credential.verify("anything") is False


@pytest.mark.parametrize("content", ["", "short", "x" * 43 + "!", "a b" * 20])
def test_a_malformed_token_file_is_no_credential_and_is_replaced(content: str) -> None:
    gate_credential.token_dir().mkdir(parents=True, mode=0o700)
    gate_credential.token_path().write_text(content)

    assert gate_credential.read_token() is None
    assert gate_credential.verify(content) is False
    token = gate_credential.ensure_token()
    assert token != content.strip()
    assert gate_credential.read_token() == token


def test_a_symlinked_token_file_is_not_read(tmp_path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("A" * 43 + "\n")
    gate_credential.token_dir().mkdir(parents=True, mode=0o700)
    gate_credential.token_path().symlink_to(elsewhere)

    assert gate_credential.read_token() is None
    assert gate_credential.verify("A" * 43) is False


def test_verify_accepts_only_the_current_token() -> None:
    token = gate_credential.ensure_token()

    assert gate_credential.verify(token) is True
    assert gate_credential.verify(token[:-1]) is False
    assert gate_credential.verify("") is False
    assert gate_credential.verify(token + "x") is False


def test_a_replaced_token_is_picked_up_without_a_restart() -> None:
    old = gate_credential.ensure_token()
    gate_credential.token_path().unlink()
    new = gate_credential.ensure_token()

    assert new != old
    assert gate_credential.verify(new) is True
    assert gate_credential.verify(old) is False
