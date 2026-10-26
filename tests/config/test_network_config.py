"""hal0.toml ``[network]`` schema: public_url and extra_origins (#2382).

The REST and WebSocket origin gates only knew ``HAL0_ALLOWED_ORIGINS``,
which lives in the installer-managed api.env block and is rewritten on every
installer run, so a hand-added reverse-proxy origin was lost on re-run.
``[network]`` is the persisted home. These tests pin the validation and
normalisation, and that a box upgraded with no ``[network]`` table loads.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pydantic
import pytest

from hal0.config.loader import load_hal0_config, save_hal0_config
from hal0.config.schema import Hal0Config, NetworkConfig, normalise_origin

# ── extra_origins ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://box.lan:8080", "http://box.lan:8080"),
        ("https://hal0.example.com/", "https://hal0.example.com"),  # trailing slash dropped
        ("HTTPS://Hal0.Example.COM", "https://hal0.example.com"),  # scheme + host lower-cased
        ("https://hal0.example.com:443", "https://hal0.example.com"),  # default port dropped
        ("http://box.lan:80", "http://box.lan"),
        ("https://box.lan:8443", "https://box.lan:8443"),
        ("http://[fd00::50]:8080", "http://[fd00::50]:8080"),
        ("  http://192.0.2.10:8080  ", "http://192.0.2.10:8080"),
        ("https://my-box.tailnet-1234.ts.net", "https://my-box.tailnet-1234.ts.net"),
    ],
)
def test_extra_origin_normalised(raw: str, expected: str) -> None:
    assert normalise_origin(raw) == expected
    assert NetworkConfig(extra_origins=[raw]).extra_origins == [expected]


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("box.lan:8080", "must start with http:// or https://"),
        ("hal0.example.com", "must start with http:// or https://"),
        ("*", "must start with http:// or https://"),
        ("null", "must start with http:// or https://"),
        ("ftp://box.lan", "must use http or https"),
        ("ws://box.lan:8080", "must use http or https"),
        ("https://hal0.example.com/app", "no path, query or fragment"),
        ("https://hal0.example.com/?x=1", "no path, query or fragment"),
        ("https://hal0.example.com#frag", "no path, query or fragment"),
        ("https://user:pw@hal0.example.com", "user name or password"),
        ("https://hal0.example.com:99999", "invalid port"),
        ("https://hal0.example.com:http", "invalid port"),
        ("http://", "has no host"),
        ("http://*.example.com", "not a valid host"),
        ("http://box lan", "must not contain whitespace"),
        ("", "must not be empty"),
    ],
)
def test_malformed_extra_origin_rejected(raw: str, reason: str) -> None:
    with pytest.raises(pydantic.ValidationError) as excinfo:
        NetworkConfig(extra_origins=[raw])
    assert reason in str(excinfo.value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # IDNA: browsers send the punycode form in Origin.
        ("https://例え.テスト", "https://xn--r8jz45g.xn--zckzah"),
        ("https://Bücher.Example", "https://xn--bcher-kva.example"),
        ("https://xn--bcher-kva.example", "https://xn--bcher-kva.example"),
        # IPv6: the WHATWG serialisation, not Python's.
        ("http://[0:0:0:0:0:0:0:1]:8080", "http://[::1]:8080"),
        ("http://[FD00:0::50]", "http://[fd00::50]"),
        ("http://[1:0:0:1:0:0:0:1]", "http://[1:0:0:1::1]"),
        ("http://[::ffff:192.0.2.1]", "http://[::ffff:c000:201]"),
        # A fully-qualified name keeps its dot, as a browser sends it.
        ("http://hal0.example.com.", "http://hal0.example.com."),
    ],
)
def test_host_canonicalised_like_a_browser(raw: str, expected: str) -> None:
    """The stored origin is the exact string a browser puts in ``Origin``."""
    assert normalise_origin(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://faß.de",  # IDNA2003 says fass.de, a browser xn--fa-hia.de: a different host
        "https://\u200dbox.example",  # ZWJ, same deviation class
        "http://0x7f.1",  # a browser reads this as 127.0.0.1
        "http://box.123",  # numeric last label: not a DNS name to a browser
        "http://127.1:8080",  # short-form IPv4
        "http://192.168.001.10",  # leading zeros
        "http://[fe80::1%25eth0]:8080",  # zone id: never sent by a browser
        "http://box.lan:0",
        "http://-bad.lan",
        "http://a..b",
        "http://a;b.lan",
        'http://a"b.lan',
        "http://a,b.lan",
        "http://a\\b",
        "http://box.lan:8080\x00",
    ],
)
def test_host_a_browser_would_not_send_is_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        normalise_origin(raw)


def test_extra_origins_deduplicated_in_order() -> None:
    cfg = NetworkConfig(
        extra_origins=[
            "http://b.lan:8080",
            "http://a.lan:8080",
            "http://B.lan:8080/",
            "http://a.lan:8080",
        ]
    )
    assert cfg.extra_origins == ["http://b.lan:8080", "http://a.lan:8080"]


def test_extra_origins_must_be_a_list() -> None:
    with pytest.raises(pydantic.ValidationError, match="not a single string"):
        NetworkConfig(extra_origins="http://box.lan:8080")  # type: ignore[arg-type]
    with pytest.raises(pydantic.ValidationError, match="must be a string"):
        NetworkConfig(extra_origins=[8080])  # type: ignore[list-item]


# ── public_url ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://hal0.example.com", "https://hal0.example.com"),
        ("https://hal0.example.com/", "https://hal0.example.com"),
        ("https://Hal0.Example.com:443/", "https://hal0.example.com"),
        ("https://hal0.example.com:8443", "https://hal0.example.com:8443"),
        ("https://example.com/hal0/", "https://example.com/hal0"),  # a sub-path proxy
        ("http://192.0.2.10:8080", "http://192.0.2.10:8080"),
        ("", None),  # an emptied Settings field means unset
        ("   ", None),
        (None, None),
    ],
)
def test_public_url_normalised(raw: str | None, expected: str | None) -> None:
    assert NetworkConfig(public_url=raw).public_url == expected


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("hal0.example.com", "must start with http:// or https://"),
        ("ftp://hal0.example.com", "must use http or https"),
        ("https://hal0.example.com/?a=1", "query or fragment"),
        ("https://hal0.example.com/#x", "query or fragment"),
        ("https://u:p@hal0.example.com", "user name or password"),
        ("https://:8443", "has no host"),
        ("https://hal0.example.com/a b", "must not contain whitespace"),
        ("https://hal0.example.com/a$(id)", "a path with characters other than"),
        ("https://hal0.example.com/a'b", "a path with characters other than"),
        ("https://faß.de", "not a valid host"),
    ],
)
def test_malformed_public_url_rejected(raw: str, reason: str) -> None:
    with pytest.raises(pydantic.ValidationError) as excinfo:
        NetworkConfig(public_url=raw)
    assert reason in str(excinfo.value)


def test_unknown_network_key_rejected() -> None:
    """``extra="forbid"``: a typo must fail loudly, not drop an origin silently."""
    with pytest.raises(pydantic.ValidationError, match="extra_origin"):
        Hal0Config.model_validate({"network": {"extra_origin": ["http://box.lan:8080"]}})


# ── upgrade: a box with no [network] table ────────────────────────────────


def test_upgrade_from_config_without_network_section(tmp_path: Path) -> None:
    """An existing hal0.toml written before [network] existed loads to defaults,
    and a later save round-trips the new section without disturbing the rest."""
    target = tmp_path / "hal0.toml"
    target.write_text(
        "[meta]\nschema_version = 1\n\n[security]\nrequire_auth = true\n\n"
        '[telemetry]\nchannel = "preview"\n',
        encoding="utf-8",
    )
    cfg = load_hal0_config(target)
    assert cfg.network.public_url is None
    assert cfg.network.extra_origins == []
    assert cfg.security.require_auth is True

    cfg.network.extra_origins = ["http://box.lan:8080"]
    cfg.network.public_url = "https://hal0.example.com"
    save_hal0_config(cfg, target)

    raw = tomllib.loads(target.read_text(encoding="utf-8"))
    assert raw["network"] == {
        "public_url": "https://hal0.example.com",
        "extra_origins": ["http://box.lan:8080"],
        "comfyui_listen": "lan",
    }

    assert raw["telemetry"]["channel"] == "preview"
    reloaded = load_hal0_config(target)
    assert reloaded.network.extra_origins == ["http://box.lan:8080"]
    assert reloaded.security.require_auth is True


def test_unset_public_url_not_written(tmp_path: Path) -> None:
    """None has no TOML form; the writer drops it and the load restores None."""
    target = tmp_path / "hal0.toml"
    save_hal0_config(Hal0Config(), target)
    raw = tomllib.loads(target.read_text(encoding="utf-8"))
    assert "public_url" not in raw.get("network", {})
    assert load_hal0_config(target).network.public_url is None


# ── comfyui_listen (ADR-0041) ─────────────────────────────────────────────


def test_comfyui_listen_defaults_to_lan() -> None:
    """A box with no ``[network]`` table, or one without the key, puts the editor on the LAN."""
    assert NetworkConfig().comfyui_listen == "lan"
    assert Hal0Config().network.comfyui_listen == "lan"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("lan", "lan"), ("loopback", "loopback"), (" LAN ", "lan"), ("Loopback", "loopback")],
)
def test_comfyui_listen_accepts_the_two_modes(raw: str, expected: str) -> None:
    assert NetworkConfig(comfyui_listen=raw).comfyui_listen == expected


@pytest.mark.parametrize("raw", ["", "0.0.0.0", "127.0.0.1", "yes", 1, None])
def test_comfyui_listen_rejects_anything_else(raw: object) -> None:
    with pytest.raises(pydantic.ValidationError, match="comfyui_listen"):
        NetworkConfig(comfyui_listen=raw)  # type: ignore[arg-type]


def test_comfyui_listen_round_trips_through_hal0_toml(tmp_path: Path) -> None:
    path = tmp_path / "hal0.toml"
    path.write_text('[network]\ncomfyui_listen = "loopback"\n', encoding="utf-8")
    cfg = load_hal0_config(path)
    assert cfg.network.comfyui_listen == "loopback"
    save_hal0_config(cfg, path)
    assert (
        tomllib.loads(path.read_text(encoding="utf-8"))["network"]["comfyui_listen"] == "loopback"
    )


def test_reach_reads_the_knob_and_fails_soft_to_lan(monkeypatch) -> None:
    """``hal0.comfyui.reach`` is the one reader; a config that cannot load means LAN, the default."""
    from hal0.comfyui import reach

    monkeypatch.setattr(
        "hal0.config.loader.load_hal0_config",
        lambda *a, **k: Hal0Config(network=NetworkConfig(comfyui_listen="loopback")),
    )
    assert reach.comfyui_listen() == "loopback"
    assert reach.editor_on_lan() is False

    def boom(*a, **k):
        raise OSError("no config")

    monkeypatch.setattr("hal0.config.loader.load_hal0_config", boom)
    assert reach.comfyui_listen() == "lan"
    assert reach.editor_on_lan() is True
