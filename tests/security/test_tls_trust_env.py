"""#2941: TLS trust variables reach ``api.env`` only as a root-owned, hal0-unwritable path.

``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` / ``REQUESTS_CA_BUNDLE`` /
``CURL_CA_BUNDLE`` pick the CA bundle every outbound TLS client in a
hal0-api child trusts. Writable through the secrets or credentials routes
with an attacker-chosen path (a bundle the ``hal0`` user can write), they
would let a man-in-the-middle read every token hal0 sends upstream. A
corporate-proxy box legitimately needs a custom CA, so the rule is not a
flat deny: the value must be an absolute path to an existing root-owned
file (a directory for ``SSL_CERT_DIR``) that ``hal0`` cannot modify, under
parents that are root-owned and not writable by group or others.

Real root ownership cannot be had in a test. The checker is never mocked:
the tests move its two environmental inputs (``_TRUSTED_UID``, the uid that
counts as root, and ``_TRUST_ANCHOR``, the directory above which the path
is assumed trusted) to the test user and ``tmp_path``.
"""

from __future__ import annotations

import os
import signal
import stat
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import _env_store
from hal0.api._env_store import TLS_TRUST_ENV_NAMES, tls_trust_violation, upsert_env_value
from hal0.upstreams.registry import Upstream

NAMES = ["SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"]


@pytest.fixture(autouse=True)
def _restore_environ() -> Iterator[None]:
    snapshot = dict(os.environ)
    yield
    for key in set(os.environ) - set(snapshot):
        del os.environ[key]
    for key, value in snapshot.items():
        os.environ[key] = value


@pytest.fixture
def trusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Treat the test user as "root" and ``tmp_path`` as the trusted top."""
    root = tmp_path.resolve() / "trust"
    root.mkdir(mode=0o755)
    monkeypatch.setattr(_env_store, "_TRUSTED_UID", os.getuid())
    monkeypatch.setattr(_env_store, "_TRUST_ANCHOR", root.parent)
    return root


def _bundle(root: Path, name: str = "ca.pem", mode: int = 0o644) -> Path:
    path = root / name
    path.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    path.chmod(mode)
    return path


# ── the checking function ────────────────────────────────────────────────────


def test_the_four_names_are_covered() -> None:
    assert {
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
    } <= set(TLS_TRUST_ENV_NAMES)


def test_node_extra_ca_certs_is_no_longer_a_flat_deny() -> None:
    from hal0.api._env_store import is_process_control_env

    assert not is_process_control_env("NODE_EXTRA_CA_CERTS")


@pytest.mark.parametrize("name", NAMES)
def test_accepts_a_root_owned_file(trusted: Path, name: str) -> None:
    assert tls_trust_violation(name, str(_bundle(trusted))) is None


def test_name_match_is_case_insensitive_and_other_names_pass(trusted: Path) -> None:
    assert tls_trust_violation("ssl_cert_file", "relative.pem") is not None
    assert tls_trust_violation("HF_TOKEN", "relative.pem") is None
    assert tls_trust_violation("SSL_CERT_FILES", "relative.pem") is None


def test_accepts_a_root_owned_directory_for_ssl_cert_dir(trusted: Path) -> None:
    d = trusted / "certs"
    d.mkdir(mode=0o755)
    assert tls_trust_violation("SSL_CERT_DIR", str(d)) is None


def test_ssl_cert_dir_accepts_a_colon_list_only_if_every_entry_passes(trusted: Path) -> None:
    good = trusted / "a"
    good.mkdir(mode=0o755)
    bad = trusted / "b"
    bad.mkdir(mode=0o777)
    bad.chmod(0o777)
    assert tls_trust_violation("SSL_CERT_DIR", f"{good}:{good}") is None
    assert tls_trust_violation("SSL_CERT_DIR", f"{good}:{bad}") is not None
    assert tls_trust_violation("SSL_CERT_DIR", f"{good}:") is not None


def test_ssl_cert_dir_refuses_a_file_and_ssl_cert_file_refuses_a_dir(trusted: Path) -> None:
    assert tls_trust_violation("SSL_CERT_DIR", str(_bundle(trusted))) is not None
    d = trusted / "certs"
    d.mkdir(mode=0o755)
    for name in NAMES:
        assert tls_trust_violation(name, str(d)) is not None


def test_refuses_a_file_not_owned_by_root(trusted: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _bundle(trusted)
    monkeypatch.setattr(_env_store, "_TRUSTED_UID", os.getuid() + 1)
    assert tls_trust_violation("SSL_CERT_FILE", str(path)) is not None


@pytest.mark.parametrize("mode", [0o664, 0o666, 0o620, 0o602])
def test_refuses_a_group_or_world_writable_file(trusted: Path, mode: int) -> None:
    path = _bundle(trusted, mode=mode)
    assert tls_trust_violation("SSL_CERT_FILE", str(path)) is not None


@pytest.mark.parametrize("mode", [0o775, 0o777, 0o757])
def test_refuses_a_writable_parent_directory(trusted: Path, mode: int) -> None:
    sub = trusted / "sub"
    sub.mkdir()
    path = _bundle(sub)
    sub.chmod(mode)
    assert tls_trust_violation("SSL_CERT_FILE", str(path)) is not None


def test_refuses_a_writable_grandparent(trusted: Path) -> None:
    deep = trusted / "a" / "b"
    deep.mkdir(parents=True)
    path = _bundle(deep)
    (trusted / "a").chmod(0o777)
    assert tls_trust_violation("SSL_CERT_FILE", str(path)) is not None


def test_refuses_a_parent_not_owned_by_root(trusted: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hal0-owned directory lets hal0 swap the file whatever the file's mode."""
    path = _bundle(trusted)
    real_lstat = os.lstat

    def lstat(p: str | os.PathLike[str], *a: object, **k: object) -> os.stat_result:
        st = real_lstat(p, *a, **k)  # type: ignore[arg-type]
        if Path(p) == trusted:
            return os.stat_result((st.st_mode, *tuple(st)[1:4], os.getuid() + 1, *tuple(st)[5:]))
        return st

    monkeypatch.setattr(os, "lstat", lstat)
    assert tls_trust_violation("SSL_CERT_FILE", str(path)) is not None


def test_refuses_a_symlink_to_a_trusted_file_from_a_writable_directory(trusted: Path) -> None:
    """The link can be repointed by whoever can write the directory it sits in."""
    target = _bundle(trusted)
    loose = trusted / "loose"
    loose.mkdir()
    link = loose / "link.pem"
    link.symlink_to(target)
    loose.chmod(0o777)
    assert tls_trust_violation("SSL_CERT_FILE", str(link)) is not None


def test_symlink_to_a_writable_target_is_refused(trusted: Path) -> None:
    loose = trusted / "loose"
    loose.mkdir()
    target = _bundle(loose)
    loose.chmod(0o777)
    link = trusted / "link.pem"
    link.symlink_to(target)
    assert tls_trust_violation("SSL_CERT_FILE", str(link)) is not None


def test_a_symlink_whose_every_component_is_trusted_resolves_and_passes(trusted: Path) -> None:
    """Distro layouts (``/etc/ssl/certs/*.pem`` links, usr-merge ``/lib``) must keep working."""
    target = _bundle(trusted)
    link = trusted / "link.pem"
    link.symlink_to(target)
    assert tls_trust_violation("SSL_CERT_FILE", str(link)) is None


def _loose(root: Path) -> Path:
    loose = root / "loose"
    loose.mkdir()
    loose.chmod(0o777)
    return loose


def test_refuses_a_chain_through_a_writable_intermediate_hop(trusted: Path) -> None:
    """(a) trusted link -> writable hop -> trusted file: hal0 repoints the middle hop."""
    target = _bundle(trusted)
    loose = _loose(trusted)
    mid = loose / "mid.pem"
    mid.symlink_to(target)
    link = trusted / "link.pem"
    link.symlink_to(mid)
    assert tls_trust_violation("SSL_CERT_FILE", str(link)) is not None


def test_refuses_a_directory_chain_through_a_writable_hop(trusted: Path) -> None:
    """(b) trusted/dlink -> writable/middir -> trusted/realdir, value dlink/ca.pem."""
    realdir = trusted / "realdir"
    realdir.mkdir(mode=0o755)
    _bundle(realdir)
    loose = _loose(trusted)
    middir = loose / "middir"
    middir.symlink_to(realdir)
    dlink = trusted / "dlink"
    dlink.symlink_to(middir)
    assert tls_trust_violation("SSL_CERT_FILE", str(dlink / "ca.pem")) is not None


def test_refuses_a_relative_hop_through_a_writable_directory(trusted: Path) -> None:
    """(c) trusted/rel.pem -> ../writable/mid.pem (relative target)."""
    target = _bundle(trusted)
    sibling = trusted.parent / "writable"
    sibling.mkdir()
    sibling.chmod(0o777)
    (sibling / "mid.pem").symlink_to(target)
    (trusted / "rel.pem").symlink_to("../writable/mid.pem")
    assert tls_trust_violation("SSL_CERT_FILE", str(trusted / "rel.pem")) is not None


def test_a_relative_hop_inside_the_trusted_tree_passes(trusted: Path) -> None:
    _bundle(trusted)
    (trusted / "rel.pem").symlink_to("./ca.pem")
    assert tls_trust_violation("SSL_CERT_FILE", str(trusted / "rel.pem")) is None


@pytest.fixture
def _alarm() -> Iterator[None]:
    """Fail instead of hanging if the walk stops terminating (main thread only)."""

    def boom(*_: object) -> None:
        raise TimeoutError("TLS trust walk did not terminate")

    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(10)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def test_dotdot_in_a_relative_symlink_target_is_honoured(trusted: Path) -> None:
    """trusted/sub/link -> ../untrusted.pem must resolve to trusted/untrusted.pem, not sub/untrusted.pem."""
    sub = trusted / "sub"
    sub.mkdir(mode=0o755)
    _bundle(sub, "untrusted.pem")  # decoy a walk that ignores '..' would land on
    loose = _loose(trusted)
    evil = _bundle(loose, "x.pem")
    # '..' climbs from sub to trusted, where the target is a symlink out to a writable file
    (trusted / "untrusted.pem").symlink_to(evil)
    (sub / "link").symlink_to("../untrusted.pem")
    assert tls_trust_violation("SSL_CERT_FILE", str(sub / "link")) is not None


def test_dotdot_in_a_relative_symlink_target_accepts_the_trusted_file(trusted: Path) -> None:
    sub = trusted / "sub"
    sub.mkdir(mode=0o755)
    _bundle(trusted, "good.pem")
    (sub / "link").symlink_to("../good.pem")
    assert tls_trust_violation("SSL_CERT_FILE", str(sub / "link")) is None


@pytest.mark.usefixtures("_alarm")
def test_a_symlink_loop_is_refused(trusted: Path) -> None:
    (trusted / "a").symlink_to(trusted / "b")
    (trusted / "b").symlink_to(trusted / "a")
    assert tls_trust_violation("SSL_CERT_FILE", str(trusted / "a")) is not None


def test_ssl_cert_dir_checks_every_entry(trusted: Path) -> None:
    d = trusted / "certs"
    d.mkdir(mode=0o755)
    ok = _bundle(d, "ok.pem")
    (d / "hash.0").symlink_to(ok)
    assert tls_trust_violation("SSL_CERT_DIR", str(d)) is None
    _bundle(d, "bad.pem", mode=0o666)
    assert tls_trust_violation("SSL_CERT_DIR", str(d)) is not None


def test_ssl_cert_dir_refuses_an_entry_symlinked_out_to_a_writable_file(trusted: Path) -> None:
    d = trusted / "certs"
    d.mkdir(mode=0o755)
    loose = _loose(trusted)
    evil = _bundle(loose, "evil.pem")
    (d / "evil.0").symlink_to(evil)
    assert tls_trust_violation("SSL_CERT_DIR", str(d)) is not None


def test_ssl_cert_dir_with_too_many_entries_is_refused(
    trusted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = trusted / "certs"
    d.mkdir(mode=0o755)
    for i in range(4):
        _bundle(d, f"{i}.pem")
    monkeypatch.setattr(_env_store, "_TLS_MAX_DIR_ENTRIES", 3)
    assert tls_trust_violation("SSL_CERT_DIR", str(d)) is not None


def test_value_size_bounds(trusted: Path) -> None:
    d = trusted / "certs"
    d.mkdir(mode=0o755)
    assert tls_trust_violation("SSL_CERT_DIR", ":".join([str(d)] * 17)) is not None
    assert tls_trust_violation("SSL_CERT_DIR", ":".join([str(d)] * 16)) is None
    assert tls_trust_violation("SSL_CERT_FILE", "/" + "a" * 5000) is not None


def test_refuses_a_dangling_symlink(trusted: Path) -> None:
    link = trusted / "link.pem"
    link.symlink_to(trusted / "gone.pem")
    assert tls_trust_violation("SSL_CERT_FILE", str(link)) is not None


@pytest.mark.parametrize("value", ["ca.pem", "./ca.pem", "../ca.pem", "~/ca.pem", "$HOME/ca.pem"])
def test_refuses_a_relative_path(trusted: Path, value: str) -> None:
    _bundle(trusted)
    assert tls_trust_violation("SSL_CERT_FILE", value) is not None


def test_refuses_a_path_that_climbs_out_through_dotdot(trusted: Path) -> None:
    sub = trusted / "sub"
    sub.mkdir()
    _bundle(trusted)
    assert tls_trust_violation("SSL_CERT_FILE", f"{sub}/../ca.pem") is not None


def test_refuses_a_missing_file(trusted: Path) -> None:
    assert tls_trust_violation("SSL_CERT_FILE", str(trusted / "nope.pem")) is not None


@pytest.mark.parametrize("value", ["", "   ", "/etc/x\x00y"])
def test_refuses_empty_and_nul_values(value: str) -> None:
    assert tls_trust_violation("SSL_CERT_FILE", value) is not None


def test_real_checker_refuses_a_user_owned_file_without_any_patching(tmp_path: Path) -> None:
    """Unpatched, a file the test user owns is not root-owned."""
    if os.getuid() == 0:
        pytest.skip("running as root")
    path = tmp_path / "ca.pem"
    path.write_text("x", encoding="utf-8")
    assert tls_trust_violation("SSL_CERT_FILE", str(path)) is not None


def test_real_checker_accepts_the_system_bundle_when_it_is_trustworthy() -> None:
    """Sanity on a real box: a stock distro bundle is not refused for a spurious reason."""
    for cand in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
        p = Path(cand)
        if not p.is_file():
            continue
        st = p.stat()
        chain = [p.resolve(), *p.resolve().parents]
        if st.st_uid != 0 or any(
            c.stat().st_uid != 0 or c.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH) for c in chain
        ):
            continue
        assert tls_trust_violation("SSL_CERT_FILE", cand) is None
        return
    pytest.skip("no stock root-owned CA bundle on this host")


def test_failure_reason_never_echoes_secret_looking_input(trusted: Path) -> None:
    msg = tls_trust_violation("SSL_CERT_FILE", "relative.pem")
    assert msg and "relative.pem" not in msg


# ── the shared writer (every api.env upsert funnels through it) ──────────────


def test_writer_guard_refuses_a_bad_tls_value_and_writes_nothing(
    trusted: Path, tmp_path: Path
) -> None:
    target = tmp_path / "api.env"
    with pytest.raises(ValueError, match="TLS trust"):
        upsert_env_value(target, "SSL_CERT_FILE", str(trusted / "missing.pem"))
    assert not target.exists()


def test_writer_guard_accepts_a_trusted_value(trusted: Path, tmp_path: Path) -> None:
    target = tmp_path / "api.env"
    bundle = _bundle(trusted)
    upsert_env_value(target, "SSL_CERT_FILE", str(bundle))
    assert f'SSL_CERT_FILE="{bundle}"' in target.read_text(encoding="utf-8")


# ── POST/PUT /api/secrets/{name} ─────────────────────────────────────────────


def _api_env(home: str) -> Path:
    return Path(home) / "etc" / "hal0" / "api.env"


@pytest.mark.parametrize("verb", ["post", "put"])
@pytest.mark.parametrize("name", [*NAMES, "SSL_CERT_DIR"])
def test_secrets_route_refuses_an_untrusted_path(
    client: TestClient, tmp_hal0_home: str, trusted: Path, name: str, verb: str
) -> None:
    bad = trusted / "loose.pem"
    bad.write_text("x", encoding="utf-8")
    bad.chmod(0o666)
    api_env = _api_env(tmp_hal0_home)
    before = api_env.read_bytes() if api_env.exists() else None
    live_before = os.environ.get(name)

    r = getattr(client, verb)(f"/api/secrets/{name}", json={"value": str(bad)})

    assert r.status_code == 403, r.text
    err = r.json()["error"]
    assert err["code"] == "secret.protected"
    assert err["details"]["reason"] == "tls_trust"
    assert err["details"]["name"] == name
    assert str(bad) not in r.text
    assert (api_env.read_bytes() if api_env.exists() else None) == before
    assert os.environ.get(name) == live_before


@pytest.mark.parametrize("name", NAMES)
def test_secrets_route_accepts_a_trusted_file(
    client: TestClient, tmp_hal0_home: str, trusted: Path, name: str
) -> None:
    bundle = _bundle(trusted)
    r = client.put(f"/api/secrets/{name}", json={"value": str(bundle)})
    assert r.status_code == 204, r.text
    assert f'{name}="{bundle}"' in _api_env(tmp_hal0_home).read_text(encoding="utf-8")
    assert os.environ[name] == str(bundle)


def test_secrets_route_accepts_a_trusted_directory_for_ssl_cert_dir(
    client: TestClient, trusted: Path
) -> None:
    d = trusted / "certs"
    d.mkdir(mode=0o755)
    r = client.put("/api/secrets/SSL_CERT_DIR", json={"value": str(d)})
    assert r.status_code == 204, r.text


def test_secrets_route_refuses_without_patching_a_user_owned_file(
    client: TestClient, tmp_path: Path
) -> None:
    if os.getuid() == 0:
        pytest.skip("running as root")
    path = tmp_path / "ca.pem"
    path.write_text("x", encoding="utf-8")
    r = client.put("/api/secrets/SSL_CERT_FILE", json={"value": str(path)})
    assert r.status_code == 403, r.text
    assert r.json()["error"]["details"]["reason"] == "tls_trust"


def test_delete_still_removes_a_planted_tls_value(client: TestClient, tmp_hal0_home: str) -> None:
    api_env = _api_env(tmp_hal0_home)
    api_env.parent.mkdir(parents=True, exist_ok=True)
    api_env.write_text('SSL_CERT_FILE="/tmp/evil.pem"\nHF_TOKEN="hf_x"\n', encoding="utf-8")
    r = client.delete("/api/secrets/SSL_CERT_FILE")
    assert r.status_code == 204, r.text
    assert "SSL_CERT_FILE" not in api_env.read_text(encoding="utf-8")


# ── POST /api/providers/{name}/credentials ───────────────────────────────────


@pytest.fixture
def provider_client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        c.app.state.upstreams.upsert(  # type: ignore[attr-defined]
            Upstream(
                name="corp",
                kind="remote",
                url="https://llm.example.invalid/v1",
                auth_style="bearer",
                auth_value_env="SSL_CERT_FILE",
            )
        )
        yield c


def test_credentials_route_refuses_an_untrusted_path(
    provider_client: TestClient, tmp_hal0_home: str, trusted: Path
) -> None:
    bad = _bundle(trusted, mode=0o666)
    live_before = os.environ.get("SSL_CERT_FILE")
    r = provider_client.post(
        "/api/providers/corp/credentials", json={"key": "SSL_CERT_FILE", "value": str(bad)}
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["details"]["reason"] == "tls_trust"
    assert not _api_env(tmp_hal0_home).exists()
    assert os.environ.get("SSL_CERT_FILE") == live_before


def test_credentials_route_accepts_a_trusted_path(
    provider_client: TestClient, tmp_hal0_home: str, trusted: Path
) -> None:
    bundle = _bundle(trusted)
    r = provider_client.post(
        "/api/providers/corp/credentials", json={"key": "SSL_CERT_FILE", "value": str(bundle)}
    )
    assert r.status_code == 200, r.text
    assert f'SSL_CERT_FILE="{bundle}"' in _api_env(tmp_hal0_home).read_text(encoding="utf-8")


# ── concurrent api.env writers must not lose keys ────────────────────────────


def _hammer(target: Path, fns: list) -> None:
    barrier = threading.Barrier(len(fns))

    def run(fn):
        barrier.wait()
        fn()

    ts = [threading.Thread(target=run, args=(fn,)) for fn in fns]
    for t in ts:
        t.start()
    for t in ts:
        t.join()


def test_concurrent_upserts_keep_every_key(tmp_path: Path) -> None:
    from hal0.api._env_store import list_env_keys

    lost = 0
    for trial in range(15):
        target = tmp_path / f"t{trial}" / "api.env"
        target.parent.mkdir()
        target.write_text("", encoding="utf-8")
        _hammer(
            target, [lambda i=i, t=target: upsert_env_value(t, f"KEY_{i}", "v") for i in range(8)]
        )
        lost += 8 - len(list_env_keys(target))
    assert lost == 0


def test_concurrent_upsert_delete_and_rotation_keep_every_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The routes' writers and ``rotate_api_env_key`` share one lock."""
    from hal0 import service_identity
    from hal0.api._env_store import delete_env_value, list_env_keys

    etc = tmp_path / "etc"
    etc.mkdir()
    monkeypatch.setattr("hal0.config.paths.etc", lambda: etc)
    monkeypatch.setattr(service_identity, "refresh_hindsight_llm_env", lambda: False)
    target = etc / "api.env"
    lost = 0
    for _ in range(10):
        target.write_text('GONE="x"\n', encoding="utf-8")
        fns = [lambda i=i: upsert_env_value(target, f"KEY_{i}", "v") for i in range(6)]
        fns.append(lambda: delete_env_value(target, "GONE"))
        fns.append(lambda: service_identity.rotate_api_env_key("admin"))
        _hammer(target, fns)
        keys = set(list_env_keys(target))
        lost += len(({f"KEY_{i}" for i in range(6)} | {"HAL0_ADMIN_KEY"}) - keys)
        assert "GONE" not in keys
    assert lost == 0
