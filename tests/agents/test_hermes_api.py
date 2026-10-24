"""Discovery and key resolution for the Hermes API server (``hermes_api``)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from hal0.agents import hermes_api
from hal0.agents import hermes_provision as hp

GOOD = "abcdefghijklmnopqrstuvwxyz012345"
VAULT = "vault_key_abcdefghijklmnopqrstuvwxyz0123456"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("HERMES_HOME", "API_SERVER_HOST", "API_SERVER_PORT", "API_SERVER_MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)
    # The default-key mirror reads hal0's root-only vault: keep it off the host.
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", tmp_path / "vault" / "hermes.env")


def _write_vault(tmp_path: Path, key: str) -> None:
    (tmp_path / "vault").mkdir(exist_ok=True)
    (tmp_path / "vault" / "hermes.env").write_text(f"API_SERVER_KEY={key}\n")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "hermes"
    (h / "profiles" / "coder").mkdir(parents=True)
    (h / "profiles" / "parked").mkdir()
    (h / "profiles" / "nokey").mkdir()
    (h / ".env").write_text("# top\nOTHER=1\n")
    (h / "profiles" / "coder" / ".env").write_text(f"FOO=bar\nAPI_SERVER_KEY={GOOD}\n")
    return h


def test_hermes_home_env_then_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from hal0.agents.hermes_provision import HERMES_HOME_DEFAULT

    assert hermes_api.hermes_home() == HERMES_HOME_DEFAULT
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert hermes_api.hermes_home() == tmp_path
    monkeypatch.setenv("HERMES_HOME", "")
    assert hermes_api.hermes_home() == HERMES_HOME_DEFAULT


def test_api_server_endpoint_defaults(home: Path) -> None:
    assert hermes_api.api_server_endpoint(home) == ("127.0.0.1", 8642)


def test_api_server_endpoint_from_hermes_env_file(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P1: the overrides Hermes honours are the ones in $HERMES_HOME/.env,
    not hal0-api's own process environment."""
    monkeypatch.setenv("API_SERVER_HOST", "10.9.9.9")  # hal0-api's env: ignored
    monkeypatch.setenv("API_SERVER_PORT", "1")
    assert hermes_api.api_server_endpoint(home) == ("127.0.0.1", 8642)
    (home / ".env").write_text("API_SERVER_HOST=0.0.0.0\nAPI_SERVER_PORT=9100\n")
    assert hermes_api.api_server_endpoint(home) == ("0.0.0.0", 9100)


def test_authority_brackets_ipv6() -> None:
    assert hermes_api.authority("127.0.0.1", 8642) == "127.0.0.1:8642"
    assert hermes_api.authority("::1", 8642) == "[::1]:8642"
    assert hermes_api.authority("[::1]", 8642) == "[::1]:8642"


def test_probe_urls_bracket_an_ipv6_host(home: Path) -> None:
    (home / "config.yaml").write_text(
        "gateway:\n  platforms:\n    api_server:\n      host: '::1'\n      port: 8642\n"
    )
    seen: list[str] = []

    def probe(url: str) -> int:
        seen.append(url)
        return 200

    assert hermes_api.gateway_alive(home, probe=probe) is True
    assert hermes_api.profile_health(home, "coder", probe=probe) == 200
    assert seen == ["http://[::1]:8642/health", "http://[::1]:8642/p/coder/health"]


def test_api_server_endpoint_from_config(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_SERVER_PORT", "9100")
    (home / "config.yaml").write_text("gateway:\n  platforms:\n    api_server:\n      port: 9000\n")
    assert hermes_api.api_server_endpoint(home) == ("127.0.0.1", 9000)


def test_profile_dirs(home: Path, tmp_path: Path) -> None:
    (home / "profiles" / "afile").write_text("x")
    assert hermes_api.profile_dirs(home) == ["coder", "nokey", "parked"]
    assert hermes_api.profile_dirs(tmp_path / "nowhere") == []


def test_served_profiles_skips_404(home: Path) -> None:
    seen: list[str] = []

    def probe(url: str) -> int:
        seen.append(url)
        return 200 if "/p/coder/" in url else 404

    assert hermes_api.served_profiles(home, probe=probe) == ["default", "coder"]
    assert "http://127.0.0.1:8642/p/coder/health" in seen


def test_served_profiles_default_only_when_every_probe_fails(home: Path) -> None:
    assert hermes_api.served_profiles(home, probe=lambda _u: 0) == ["default"]


class _StatusServer:
    """A loopback HTTP server answering every GET with a fixed status."""

    def __init__(self, status: int) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        code = status

        class _H(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.mark.parametrize("status", [200, 404, 503])
def test_probe_status_returns_the_real_status(status: int) -> None:
    srv = _StatusServer(status)
    try:
        assert hermes_api.probe_status(f"http://127.0.0.1:{srv.port}/p/x/health") == status
    finally:
        srv.close()


def test_probe_status_is_zero_on_transport_error() -> None:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert hermes_api.probe_status(f"http://127.0.0.1:{port}/health") == 0


def test_gateway_alive_probes_default_health(home: Path) -> None:
    seen: list[str] = []

    def probe(url: str) -> int:
        seen.append(url)
        return 200

    assert hermes_api.gateway_alive(home, probe=probe) is True
    assert seen == ["http://127.0.0.1:8642/health"]
    assert hermes_api.gateway_alive(home, probe=lambda _u: 503) is False
    assert hermes_api.gateway_alive(home, probe=lambda _u: 0) is False


def test_api_key_for_default_reads_dotenv(home: Path) -> None:
    (home / ".env").write_text(f'API_SERVER_KEY="{GOOD}"\n')
    assert hermes_api.api_key_for("default", home) == GOOD


def test_api_key_for_default_prefers_config_key(home: Path) -> None:
    (home / ".env").write_text(f"API_SERVER_KEY={GOOD}\n")
    cfg = "z" * 20
    (home / "config.yaml").write_text(
        f"gateway:\n  platforms:\n    api_server:\n      key: {cfg}\n"
    )
    assert hermes_api.api_key_for("default", home) == cfg


def test_api_key_for_default_unusable_config_key_falls_back(home: Path) -> None:
    (home / ".env").write_text(f"API_SERVER_KEY={GOOD}\n")
    (home / "config.yaml").write_text(
        "gateway:\n  platforms:\n    api_server:\n      key: changeme\n"
    )
    assert hermes_api.api_key_for("default", home) == GOOD


def test_api_key_for_named_reads_profile_env(home: Path) -> None:
    assert hermes_api.api_key_for("coder", home) == GOOD
    assert hermes_api.api_key_for("parked", home) is None


def test_api_key_for_rejects_placeholder(home: Path) -> None:
    (home / "profiles" / "coder" / ".env").write_text("API_SERVER_KEY=changeme\n")
    assert hermes_api.api_key_for("coder", home) is None


def test_api_key_for_rejects_short(home: Path) -> None:
    (home / "profiles" / "coder" / ".env").write_text("API_SERVER_KEY=tooshort\n")
    assert hermes_api.api_key_for("coder", home) is None


def test_dotenv_parsing_rules(home: Path) -> None:
    env = home / "profiles" / "coder" / ".env"
    env.write_text(f"  # c\n\nAPI_SERVER_KEY = {GOOD} # trailing\n")
    assert hermes_api.api_key_for("coder", home) == GOOD
    env.write_text(f"API_SERVER_KEY='{GOOD} # kept'\n")
    assert hermes_api.api_key_for("coder", home) == f"{GOOD} # kept"


def test_dotenv_quoted_value_with_inline_comment(home: Path) -> None:
    """Codex P2: ``"<key>" # rotated`` must yield the key, not ``"<key>"``."""
    env = home / "profiles" / "coder" / ".env"
    env.write_text(f'API_SERVER_KEY="{GOOD}" # rotated\n')
    assert hermes_api.api_key_for("coder", home) == GOOD
    env.write_text(f"API_SERVER_KEY='{GOOD}' #x\n")
    assert hermes_api.api_key_for("coder", home) == GOOD
    env.write_text('API_SERVER_KEY="ab\\"cd\\\\ef_abcdefghijklmnop"\n')
    assert hermes_api.api_key_for("coder", home) == 'ab"cd\\ef_abcdefghijklmnop'
    env.write_text(f"API_SERVER_KEY={GOOD}#notacomment\n")
    assert hermes_api.api_key_for("coder", home) == f"{GOOD}#notacomment"


def test_key_writes_serialise_under_a_file_lock(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A periodic hal0-api sweep and a CLI provision may overlap; both check
    and append under the same cross-process lock on the target ``.env``."""
    locked: list[str] = []
    real = hermes_api.file_lock

    def spy(target: Path):
        locked.append(str(target))
        return real(target)

    monkeypatch.setattr(hermes_api, "file_lock", spy)
    assert hermes_api.ensure_profile_key("nokey", home) is True
    _write_vault(tmp_path, VAULT)
    assert hermes_api.ensure_default_key(home) is True
    assert locked == [str(home / "profiles" / "nokey" / ".env"), str(home / ".env")]
    assert hermes_api.ensure_profile_key("coder", home) is False  # keyed: no lock taken
    assert len(locked) == 2


def test_ensure_profile_key_writes_when_absent(home: Path) -> None:
    env = home / "profiles" / "nokey" / ".env"
    env.write_text("A=1\n# keep\nB=two")
    before = env.read_bytes()
    os.chmod(env, 0o640)
    assert hermes_api.ensure_profile_key("nokey", home) is True
    data = env.read_text()
    lines = [ln for ln in data.splitlines() if ln.startswith("API_SERVER_KEY=")]
    assert len(lines) == 1 and len(lines[0].split("=", 1)[1]) >= 32
    assert data.startswith(before.decode())
    assert stat.S_IMODE(env.stat().st_mode) == 0o640
    assert hermes_api.api_key_for("nokey", home) is not None


def test_ensure_profile_key_creates_0600(home: Path) -> None:
    env = home / "profiles" / "parked" / ".env"
    assert not env.exists()
    assert hermes_api.ensure_profile_key("parked", home) is True
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert env.read_text().count("API_SERVER_KEY=") == 1


def test_ensure_profile_key_never_overwrites(home: Path) -> None:
    env = home / "profiles" / "coder" / ".env"
    before = env.read_bytes()
    assert hermes_api.ensure_profile_key("coder", home) is False
    assert env.read_bytes() == before


def test_ensure_profile_key_refuses_default(home: Path) -> None:
    before = (home / ".env").read_bytes()
    assert hermes_api.ensure_profile_key("default", home) is False
    assert (home / ".env").read_bytes() == before


def test_ensure_profile_keys_returns_written_names(home: Path) -> None:
    assert hermes_api.ensure_profile_keys(home) == ["nokey", "parked"]
    assert hermes_api.ensure_profile_keys(home) == []
    assert not hermes_api.api_key_for("default", home)  # no vault: default never minted


def test_ensure_profile_keys_mirrors_default_first(home: Path, tmp_path: Path) -> None:
    _write_vault(tmp_path, VAULT)
    assert hermes_api.ensure_profile_keys(home) == ["default", "nokey", "parked"]
    assert hermes_api.api_key_for("default", home) == VAULT
    assert hermes_api.ensure_profile_keys(home) == []


def test_ensure_default_key_mirrors_given_value_appending(home: Path) -> None:
    """The provisioner hands over the key it just minted; other lines stay put."""
    env = home / ".env"
    os.chmod(env, 0o640)
    assert hermes_api.ensure_default_key(home, value=GOOD) is True
    assert env.read_text() == f"# top\nOTHER=1\nAPI_SERVER_KEY={GOOD}\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o640
    assert hermes_api.api_key_for("default", home) == GOOD


def test_ensure_default_key_reads_vault_when_no_value(home: Path, tmp_path: Path) -> None:
    _write_vault(tmp_path, VAULT)
    assert hermes_api.ensure_default_key(home) is True
    assert hermes_api.api_key_for("default", home) == VAULT


def test_ensure_default_key_never_mints(home: Path) -> None:
    """No value and no readable vault: nothing is written, the file is untouched."""
    before = (home / ".env").read_bytes()
    assert hermes_api.ensure_default_key(home) is False
    assert (home / ".env").read_bytes() == before


def test_ensure_default_key_refuses_weak_vault_key(home: Path, tmp_path: Path) -> None:
    _write_vault(tmp_path, "changeme")
    assert hermes_api.ensure_default_key(home) is False
    _write_vault(tmp_path, "short")
    assert hermes_api.ensure_default_key(home, value="short") is False
    assert "API_SERVER_KEY" not in (home / ".env").read_text()


def test_ensure_default_key_never_overwrites(home: Path, tmp_path: Path) -> None:
    (home / ".env").write_text(f"API_SERVER_KEY={GOOD}\n")
    _write_vault(tmp_path, VAULT)
    assert hermes_api.ensure_default_key(home) is False
    assert hermes_api.ensure_default_key(home, value=VAULT) is False
    assert hermes_api.api_key_for("default", home) == GOOD


def test_ensure_default_key_prefers_config_key(home: Path, tmp_path: Path) -> None:
    (home / "config.yaml").write_text(
        f"gateway:\n  platforms:\n    api_server:\n      key: {GOOD}\n"
    )
    _write_vault(tmp_path, VAULT)
    assert hermes_api.ensure_default_key(home) is False
    assert "API_SERVER_KEY" not in (home / ".env").read_text()


def test_new_env_file_written_as_root_is_owned_like_its_directory(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root --repair must not leave a root:root 0600 .env the hal0 gateway cannot read."""
    chowned: list[tuple[str, int, int]] = []
    pdir = home / "profiles" / "parked"
    monkeypatch.setattr(hermes_api.os, "geteuid", lambda: 0)
    # The write goes by descriptor (#2975): record which file each fchown hit.
    monkeypatch.setattr(
        hermes_api.os,
        "fchown",
        lambda fd, uid, gid: chowned.append((os.readlink(f"/proc/self/fd/{fd}"), uid, gid)),
    )
    assert hermes_api.ensure_profile_key("parked", home) is True
    st = pdir.stat()
    env_chowns = [c for c in chowned if not c[0].endswith(".lock")]
    assert len(env_chowns) == 1 and env_chowns[0][1:] == (st.st_uid, st.st_gid)
    assert env_chowns[0][0].startswith(str(pdir / "..env."))  # the temp file, before the rename


def test_env_key_name() -> None:
    assert hermes_api.env_key_name("hal0-brain") == "HAL0_HERMES_API_KEY__HAL0_BRAIN"


def test_model_id_for() -> None:
    assert hermes_api.model_id_for("default") == "hermes-agent"
    assert hermes_api.model_id_for("coder") == "coder"


def test_upstream_name_roundtrip() -> None:
    assert hermes_api.upstream_name_for("default") == "hermes"
    assert hermes_api.upstream_name_for("coder") == "hermes.coder"
    assert hermes_api.profile_of_upstream("hermes") == "default"
    assert hermes_api.profile_of_upstream("hermes.coder") == "coder"
    assert hermes_api.profile_of_upstream("openrouter") is None
    assert hermes_api.profile_of_upstream("hermes.") is None


def test_ensure_profile_keys_skips_unreadable_env(home: Path) -> None:
    # A directory named .env raises IsADirectoryError on read, whatever the euid.
    (home / "profiles" / "nokey" / ".env").mkdir()
    assert hermes_api.ensure_profile_key("nokey", home) is False
    assert hermes_api.ensure_profile_keys(home) == ["parked"]


def test_ensure_profile_keys_skips_permission_denied_env(home: Path) -> None:
    if os.getuid() == 0:
        pytest.skip("root reads any file")
    env = home / "profiles" / "nokey" / ".env"
    env.write_text("A=1\n")
    env.chmod(0o000)
    try:
        assert hermes_api.ensure_profile_keys(home) == ["parked"]
    finally:
        env.chmod(0o600)


def test_ensure_profile_keys_skips_unwritable_profile_dir(home: Path) -> None:
    if os.getuid() == 0:
        pytest.skip("root writes anywhere")
    # "nokey" sorts before "parked": a write failure there must not abort the sweep.
    locked = home / "profiles" / "nokey"
    locked.chmod(0o500)
    try:
        assert hermes_api.ensure_profile_key("nokey", home) is False
        assert hermes_api.ensure_profile_keys(home) == ["parked"]
    finally:
        locked.chmod(0o700)
    assert hermes_api.api_key_for("parked", home) is not None


def test_default_model_id_override_precedence(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert hermes_api.default_model_id(home) == "hermes-agent"
    monkeypatch.setenv("API_SERVER_MODEL_NAME", "hal0-api-env")  # not the gateway's env
    assert hermes_api.model_id_for("default", home) == "hermes-agent"
    (home / ".env").write_text("OTHER=1\nAPI_SERVER_MODEL_NAME=from-env\n")
    assert hermes_api.model_id_for("default", home) == "from-env"
    (home / "config.yaml").write_text(
        "gateway:\n  platforms:\n    api_server:\n      model_name: from-config\n"
    )
    assert hermes_api.model_id_for("default", home) == "from-env"  # env > config
    (home / ".env").write_text("OTHER=1\n")
    assert hermes_api.model_id_for("default", home) == "from-config"
    # Named profiles keep their name: Hermes resolves them with an empty override.
    assert hermes_api.model_id_for("coder", home) == "coder"


@pytest.mark.parametrize("as_root", [False, True])
def test_default_key_mirror_refuses_a_symlinked_hermes_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_root: bool
) -> None:
    """#2975: the root prelude must not append the vault key through a swapped .hermes."""
    state = tmp_path / "var-lib-hal0"
    state.mkdir()
    state.chmod(0o2775)  # hal0-owned, group-writable, not sticky
    victim = tmp_path / "etc"
    victim.mkdir()
    (victim / ".env").write_text("ROOT_ONLY=1\n")
    (state / ".hermes").symlink_to(victim)
    if as_root:
        monkeypatch.setattr(hermes_api.os, "geteuid", lambda: 0)
        monkeypatch.setattr(hermes_api.os, "fchown", lambda *_a: None)
    assert hermes_api.ensure_default_key(state / ".hermes", value=GOOD) is False
    monkeypatch.undo()
    assert (victim / ".env").read_text() == "ROOT_ONLY=1\n"
    # Not even the lock file lands in the link's target.
    assert sorted(p.name for p in victim.iterdir()) == [".env"]
