"""Which Hermes gateway platforms a box uses, and the upstream extras they need.

A Hermes release is built from upstream's lock with the pin's extras
(``web``, ``mcp``). The messaging SDKs live in further extras that upstream
installs lazily, at first use, through ``pm.ensure_import(<extra>)``
(``plugins/platforms/telegram/adapter.py:336-353``,
``gateway/platform_registry.py:396-417`` at d923a5eb). In a hal0 release that
lazy install cannot run: ``pm.client.sync_venv`` refuses an extra for a
process that is not running from pm's own dependency environment
(``pm/client.py:208-223``), so the platform logs "requirements not met" and
never connects. The built-in adapters (API server, webhook and others) have no
lazy path at all and need ``aiohttp``, which only an extra pulls in
(``gateway/run.py:3312-3330``).

So the builder chooses the extras itself, from two sources:

* :func:`configured_platform_extras`: the platforms this box configures,
  read the way upstream's gateway enables them: credentials in the
  environment (``gateway/config_env.py:37-58``), with an explicit
  ``enabled:`` in a config.yaml platform block winning either way
  (``gateway/config_loader.py:138-171``).
* :func:`installed_platform_extras`: the extras whose packages are already in
  the live venv, read from its site-packages and never imported, as
  upstream's own first-generation migration carries them
  (``pm/extras.py:264-296``). Packages an operator installed by hand count.

Only the extras in :data:`PLATFORM_EXTRAS` are ever chosen. ``matrix`` is
deliberately absent: it pulls ``python-olm``, which builds from source, and
upstream's matrix adapter degrades without it (``pyproject.toml:274-287``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

#: The ``aiohttp``-only extra (``sms = ["aiohttp==3.14.3"]``,
#: ``pyproject.toml:403`` at d923a5eb), which upstream's ``[all]`` carries.
AIOHTTP_EXTRA = "sms"

#: Upstream extra -> the import names that prove it is installed
#: (``pm/extras.py:17-67`` at d923a5eb; ``sms`` has no entry there, and its
#: only package is aiohttp).
EXTRA_ANCHORS: dict[str, tuple[str, ...]] = {
    "telegram": ("telegram",),
    "discord": ("discord",),
    "slack": ("slack_bolt",),
    AIOHTTP_EXTRA: ("aiohttp",),
}

#: Gateway platform -> the extra its adapter needs.
PLATFORM_EXTRAS: dict[str, str] = {
    "telegram": "telegram",
    "discord": "discord",
    "slack": "slack",
    "sms": AIOHTTP_EXTRA,
    "mattermost": AIOHTTP_EXTRA,
    "api_server": AIOHTTP_EXTRA,
    "webhook": AIOHTTP_EXTRA,
    "msgraph_webhook": AIOHTTP_EXTRA,
    "whatsapp_cloud": AIOHTTP_EXTRA,
    "bluebubbles": AIOHTTP_EXTRA,
    "weixin": AIOHTTP_EXTRA,
    "qqbot": AIOHTTP_EXTRA,
}

#: Gateway platform -> the environment names that must all be set for
#: upstream to enable it from the environment (``gateway/config_env.py:37-58``,
#: ``:306-332``).
ENV_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "telegram": ("TELEGRAM_BOT_TOKEN",),
    "discord": ("DISCORD_BOT_TOKEN",),
    "slack": ("SLACK_BOT_TOKEN",),
    "sms": ("TWILIO_ACCOUNT_SID",),
    "mattermost": ("MATTERMOST_TOKEN",),
    "api_server": ("API_SERVER_KEY",),
    "webhook": ("WEBHOOK_ENABLED",),
    "msgraph_webhook": ("MSGRAPH_WEBHOOK_ENABLED",),
    "whatsapp_cloud": ("WHATSAPP_CLOUD_PHONE_NUMBER_ID", "WHATSAPP_CLOUD_ACCESS_TOKEN"),
    "bluebubbles": ("BLUEBUBBLES_SERVER_URL", "BLUEBUBBLES_PASSWORD"),
    "weixin": ("WEIXIN_TOKEN", "WEIXIN_ACCOUNT_ID"),
    "qqbot": ("QQ_APP_ID", "QQ_CLIENT_SECRET"),
}

#: Flags upstream reads as booleans rather than as credentials.
_BOOLEAN_FLAGS = frozenset({"WEBHOOK_ENABLED", "MSGRAPH_WEBHOOK_ENABLED"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def read_env_file(path: Path) -> dict[str, str]:
    """``KEY=VALUE`` lines of a dotenv or systemd environment file; ``{}`` if unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("export "):
            s = s[len("export ") :].strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def _env_set(env: Mapping[str, str], name: str) -> bool:
    value = (env.get(name) or "").strip()
    if name in _BOOLEAN_FLAGS:
        return value.lower() in _TRUTHY
    return bool(value)


def _explicit_enabled(config: Mapping[str, Any] | None) -> dict[str, bool]:
    """Platform -> its explicit ``enabled:``, merged in upstream's order.

    ``gateway.platforms.*``, then top-level ``platforms.*``, then
    ``gateway.<platform>``; a later block wins (``gateway/config_loader.py:138-171``).
    """
    if not isinstance(config, Mapping):
        return {}
    gateway = config.get("gateway")
    gateway = gateway if isinstance(gateway, Mapping) else {}
    sources = [
        gateway.get("platforms"),
        config.get("platforms"),
        {k: v for k, v in gateway.items() if k != "platforms" and k in PLATFORM_EXTRAS},
    ]
    found: dict[str, bool] = {}
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for name, block in source.items():
            if isinstance(block, Mapping) and "enabled" in block:
                raw = block["enabled"]
                if isinstance(raw, str):
                    found[str(name)] = raw.strip().lower() in _TRUTHY
                else:
                    found[str(name)] = bool(raw)
    return found


def configured_platforms(
    config: Mapping[str, Any] | None, envs: Iterable[Mapping[str, str]]
) -> list[str]:
    """The gateway platforms in :data:`PLATFORM_EXTRAS` this box enables, sorted.

    A platform is enabled by an explicit ``enabled: true`` block, or by its
    credentials in any of ``envs`` unless a block says ``enabled: false``.
    """
    merged: dict[str, str] = {}
    for env in envs:
        merged.update({k: v for k, v in env.items() if (v or "").strip()})
    explicit = _explicit_enabled(config)
    out = []
    for platform, creds in ENV_CREDENTIALS.items():
        on = explicit.get(platform)
        if on is None:
            on = all(_env_set(merged, name) for name in creds)
        if on:
            out.append(platform)
    return sorted(out)


def extras_for(platforms: Iterable[str]) -> tuple[str, ...]:
    """The upstream extras ``platforms`` need, sorted and without repeats."""
    return tuple(sorted({PLATFORM_EXTRAS[p] for p in platforms if p in PLATFORM_EXTRAS}))


def configured_platform_extras(hermes_home: Path, secrets_env: Path | None) -> tuple[str, ...]:
    """The extras for the platforms configured in ``hermes_home`` and the vault.

    Reads ``config.yaml`` and ``.env`` under ``hermes_home`` and the gateway's
    secrets vault ``secrets_env`` (the unit's ``EnvironmentFile=``). The vault
    is ``0600 root:root``, so only a root caller sees what it holds; a
    ``hal0`` caller falls back on :func:`installed_platform_extras`.
    """
    from hal0.agents import hermes_provision as hp

    home = Path(hermes_home)
    try:
        config = hp._parse_yaml_config((home / "config.yaml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        config = None
    envs = [read_env_file(home / ".env")]
    if secrets_env is not None:
        envs.append(read_env_file(Path(secrets_env)))
    return extras_for(configured_platforms(config, envs))


def _site_packages(venv: Path) -> list[Path]:
    return [p for p in Path(venv).glob("lib/python*/site-packages") if p.is_dir()]


def _installed_in(site_packages: Path, anchor: str) -> bool:
    """Is ``anchor`` installed under ``site_packages``? Read, never imported."""
    *parents, leaf = anchor.split(".")
    parent = site_packages.joinpath(*parents)
    if (parent / leaf).is_dir():
        return True
    return any(
        entry.name.rsplit(".", 1)[-1] in {"py", "so", "pyd"} for entry in parent.glob(f"{leaf}.*")
    )


def has_extra(venv: Path, extra: str) -> bool:
    """Whether every anchor of ``extra`` is installed in ``venv``, however it got there."""
    anchors = EXTRA_ANCHORS.get(extra, (extra.replace("-", "_"),))
    return any(all(_installed_in(sp, a) for a in anchors) for sp in _site_packages(venv))


def installed_platform_extras(venv: Path) -> tuple[str, ...]:
    """The extras in :data:`EXTRA_ANCHORS` already installed in ``venv``."""
    return tuple(sorted(e for e in EXTRA_ANCHORS if has_extra(venv, e)))


def missing_extras(venv: Path, extras: Iterable[str]) -> list[str]:
    """Those of ``extras`` the venv lacks, sorted."""
    return sorted({e for e in extras if not has_extra(venv, e)})
