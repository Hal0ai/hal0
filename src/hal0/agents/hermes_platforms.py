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
  environment (``gateway/config_env.py:37-58`` and ``:511-646``), with an
  explicit ``enabled:`` in a config.yaml platform block winning either way
  (``gateway/config_loader.py:138-171`` and ``:335-372``). hal0 itself
  provisions the gateway API server on every box (``API_SERVER_KEY``), so
  that platform always counts.
* :func:`installed_platform_extras`: the extras whose packages are already in
  the live venv, read from its site-packages and never imported, as
  upstream's own first-generation migration carries them
  (``pm/extras.py:264-296``). Packages an operator installed by hand count.

Only the extras in :data:`PLATFORM_EXTRAS` are ever chosen. ``matrix`` is
deliberately absent: it pulls ``python-olm``, which builds from source, and
upstream's matrix adapter degrades without it (``pyproject.toml:274-287``).
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 — fixed argv to hal0's own seam
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

#: The ``aiohttp``-only extra (``sms = ["aiohttp==3.14.3"]``,
#: ``pyproject.toml:403`` at d923a5eb). Upstream uses it the same way: its
#: WhatsApp adapter lazily installs ``sms`` for aiohttp
#: (``plugins/platforms/whatsapp/adapter.py:300``), and ``[all]`` carries it.
AIOHTTP_EXTRA = "sms"

#: Upstream extra -> the import names that prove it is installed
#: (``pm/extras.py:17-67`` at d923a5eb; ``sms`` has no entry there, and its
#: only package is aiohttp).
EXTRA_ANCHORS: dict[str, tuple[str, ...]] = {
    "telegram": ("telegram",),
    "discord": ("discord",),
    "slack": ("slack_bolt",),
    "teams": ("microsoft_teams.apps",),
    "dingtalk": ("dingtalk_stream",),
    "feishu": ("lark_oapi",),
    "wecom": ("defusedxml",),
    "google-chat": ("google.cloud.pubsub_v1",),
    AIOHTTP_EXTRA: ("aiohttp",),
}

#: Gateway platform -> the extras its adapter needs (the extra its
#: ``ensure_deps_fn`` lazily installs, or aiohttp for the adapters that only
#: probe for it).
PLATFORM_EXTRAS: dict[str, tuple[str, ...]] = {
    "telegram": ("telegram",),
    "discord": ("discord",),
    "slack": ("slack",),
    "teams": ("teams",),
    "dingtalk": ("dingtalk",),
    "feishu": ("feishu",),
    "wecom": (AIOHTTP_EXTRA,),
    "wecom_callback": ("wecom", AIOHTTP_EXTRA),
    "google_chat": ("google-chat",),
    "sms": (AIOHTTP_EXTRA,),
    "whatsapp": (AIOHTTP_EXTRA,),
    "line": (AIOHTTP_EXTRA,),
    "mattermost": (AIOHTTP_EXTRA,),
    "api_server": (AIOHTTP_EXTRA,),
    "webhook": (AIOHTTP_EXTRA,),
    "msgraph_webhook": (AIOHTTP_EXTRA,),
    "whatsapp_cloud": (AIOHTTP_EXTRA,),
    "bluebubbles": (AIOHTTP_EXTRA,),
    "weixin": (AIOHTTP_EXTRA,),
    "qqbot": (AIOHTTP_EXTRA,),
}

#: Gateway platform -> the environment gate upstream enables it on. Every
#: entry must be set; an inner tuple is satisfied by any one of its names
#: (``_Cred.creds``, ``gateway/config_env.py:230-232``), and the plugin
#: platforms' ``required_env`` lists.
ENV_CREDENTIALS: dict[str, tuple[str | tuple[str, ...], ...]] = {
    "telegram": ("TELEGRAM_BOT_TOKEN",),
    "discord": ("DISCORD_BOT_TOKEN",),
    "slack": ("SLACK_BOT_TOKEN",),
    "teams": ("TEAMS_CLIENT_ID", "TEAMS_CLIENT_SECRET", "TEAMS_TENANT_ID"),
    "dingtalk": ("DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET"),
    "feishu": ("FEISHU_APP_ID", "FEISHU_APP_SECRET"),
    "wecom": ("WECOM_BOT_ID", "WECOM_SECRET"),
    "wecom_callback": ("WECOM_CALLBACK_CORP_ID", "WECOM_CALLBACK_CORP_SECRET"),
    "google_chat": ("GOOGLE_CHAT_SERVICE_ACCOUNT_JSON",),
    "sms": ("TWILIO_ACCOUNT_SID",),
    "whatsapp": ("WHATSAPP_ENABLED",),
    "line": ("LINE_CHANNEL_ACCESS_TOKEN", "LINE_CHANNEL_SECRET"),
    "mattermost": ("MATTERMOST_TOKEN",),
    "api_server": ("API_SERVER_KEY",),
    "webhook": ("WEBHOOK_ENABLED",),
    "msgraph_webhook": ("MSGRAPH_WEBHOOK_ENABLED",),
    "whatsapp_cloud": ("WHATSAPP_CLOUD_PHONE_NUMBER_ID", "WHATSAPP_CLOUD_ACCESS_TOKEN"),
    "bluebubbles": ("BLUEBUBBLES_SERVER_URL", "BLUEBUBBLES_PASSWORD"),
    "weixin": (("WEIXIN_TOKEN", "WEIXIN_ACCOUNT_ID"),),
    "qqbot": (("QQ_APP_ID", "QQ_CLIENT_SECRET"),),
}

#: Platforms hal0 configures on every box: provisioning always writes a strong
#: ``API_SERVER_KEY`` into the gateway's vault
#: (``hermes_provision.ensure_gateway_api_server_key``), and a fresh install
#: builds the release before that phase runs. Only an explicit
#: ``enabled: false`` block turns one off.
HAL0_MANAGED_PLATFORMS: tuple[str, ...] = ("api_server",)

#: Flags upstream reads as booleans rather than as credentials.
_BOOLEAN_FLAGS = frozenset({"WEBHOOK_ENABLED", "MSGRAPH_WEBHOOK_ENABLED", "WHATSAPP_ENABLED"})
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


Runner = Callable[..., subprocess.CompletedProcess]


def read_vault(
    path: Path, *, seam: str | None = None, run: Runner = subprocess.run
) -> dict[str, str]:
    """The gateway vault's keys, for deciding which platforms are configured.

    Root reads the file. The vault is ``0600 root:root``, so a ``hal0``
    caller (hal0-api running the converge pass) asks the ``hal0-agentenv``
    seam's ``secret-keys`` verb instead, which prints only the names of the
    keys that are set, never a value. Those come back as ``{name: "1"}``. An
    absent vault, or a seam that is missing or older than the verb, reads as
    ``{}``.
    """
    path = Path(path)
    if os.access(path, os.R_OK):
        return read_env_file(path)
    if seam is None or not path.exists():
        return {}
    try:
        proc = run(  # nosec B603 — fixed argv; the seam re-validates the agent name
            ["sudo", "-n", seam, "secret-keys", "hermes"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    names = (line.strip() for line in (proc.stdout or "").splitlines())
    return {name: "1" for name in names if name and "=" not in name}


def _env_set(env: Mapping[str, str], name: str) -> bool:
    value = (env.get(name) or "").strip()
    if name in _BOOLEAN_FLAGS:
        return value.lower() in _TRUTHY
    return bool(value)


def _gate_met(env: Mapping[str, str], gate: tuple[str | tuple[str, ...], ...]) -> bool:
    return all(
        any(_env_set(env, n) for n in group) if isinstance(group, tuple) else _env_set(env, group)
        for group in gate
    )


def _explicit_enabled(config: Mapping[str, Any] | None) -> dict[str, bool]:
    """Platform -> its explicit ``enabled:``, merged in upstream's order.

    ``gateway.platforms.*``, then top-level ``platforms.*``, then
    ``gateway.<platform>`` (``gateway/config_loader.py:138-171``), then a
    top-level ``<platform>:`` block, whose ``enabled`` upstream applies last
    (``gateway/config_loader.py:335-372``). A later block wins.
    """
    if not isinstance(config, Mapping):
        return {}
    gateway = config.get("gateway")
    gateway = gateway if isinstance(gateway, Mapping) else {}
    sources = [
        gateway.get("platforms"),
        config.get("platforms"),
        {k: v for k, v in gateway.items() if k in PLATFORM_EXTRAS},
        {k: v for k, v in config.items() if k in PLATFORM_EXTRAS},
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
    config: Mapping[str, Any] | None,
    envs: Iterable[Mapping[str, str]],
    *,
    always: Iterable[str] = (),
) -> list[str]:
    """The gateway platforms in :data:`PLATFORM_EXTRAS` this box enables, sorted.

    A platform is enabled by an explicit ``enabled: true`` block, by its
    credentials in any of ``envs``, or by being in ``always``; an explicit
    ``enabled: false`` block wins over the other two.
    """
    merged: dict[str, str] = {}
    for env in envs:
        merged.update({k: v for k, v in env.items() if (v or "").strip()})
    explicit = _explicit_enabled(config)
    forced = set(always)
    out = []
    for platform, gate in ENV_CREDENTIALS.items():
        on = explicit.get(platform)
        if on is None:
            on = platform in forced or _gate_met(merged, gate)
        if on:
            out.append(platform)
    return sorted(out)


def extras_for(platforms: Iterable[str]) -> tuple[str, ...]:
    """The upstream extras ``platforms`` need, sorted and without repeats."""
    return tuple(sorted({e for p in platforms for e in PLATFORM_EXTRAS.get(p, ())}))


def configured_platform_extras(
    hermes_home: Path,
    secrets_env: Path | None,
    *,
    seam: str | None = None,
    run: Runner = subprocess.run,
) -> tuple[str, ...]:
    """The extras for the platforms configured in ``hermes_home`` and the vault.

    Reads ``config.yaml`` and ``.env`` under ``hermes_home`` and the gateway's
    secrets vault ``secrets_env`` (the unit's ``EnvironmentFile=``), the latter
    through :func:`read_vault`. :data:`HAL0_MANAGED_PLATFORMS` always count.
    """
    from hal0.agents import hermes_provision as hp

    home = Path(hermes_home)
    try:
        config = hp._parse_yaml_config((home / "config.yaml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        config = None
    envs = [read_env_file(home / ".env")]
    if secrets_env is not None:
        envs.append(read_vault(Path(secrets_env), seam=seam, run=run))
    return extras_for(configured_platforms(config, envs, always=HAL0_MANAGED_PLATFORMS))


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


# ── upstream's own lazy-install generations ──────────────────────────────────
#
# A lazy install that does succeed (the first one a process attempts, while it
# still runs from the release venv) does not touch the release: pm builds a new
# "dependency generation" under ``$HERMES_HOME/installs/<install key>/
# environments/<id>/venv`` and records it in that install's ``facts.json``
# (``pm/environments.py:20-38``, ``pm/lock.py:269-279``). Every later
# ``hermes`` process for that checkout boots into the generation instead of the
# release venv (``hermes_bootstrap.py:611-613`` ->
# ``pm/environments.py:449-512``). The generation holds only the extras pm
# selected, starting from none (``pm/install.py:749-762``), so it lacks the
# pin's ``mcp``; hal0's release check probes the release venv and never sees it.


def install_key(src: Path) -> str:
    """Upstream's per-checkout key: the resolved checkout path, hashed
    (``pm/environments.py:20-22``). A release's ``src`` resolves to its own
    directory, so every release has its own key."""
    import hashlib

    return hashlib.sha256(str(Path(src).resolve()).encode("utf-8")).hexdigest()[:16]


def committed_generation(hermes_home: Path, src: Path) -> Path | None:
    """The pm generation Hermes boots into for ``src``, or ``None``.

    Read from ``<hermes_home>/installs/<key>/facts.json`` the way upstream's
    ``_recorded_venv`` reads it (``pm/environments.py:297-318``), never imported.
    """
    import json

    state = Path(hermes_home) / "installs" / install_key(src)
    try:
        data = json.loads((state / "facts.json").read_text(encoding="utf-8-sig"))
        value = data["packages"]["venv"]["environment"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(value, str):
        return None
    environment = Path(value)
    generations = state / "environments"
    try:
        inside = environment.resolve().is_relative_to(generations.resolve())
    except OSError:
        return None
    if not inside or not (environment / "pyvenv.cfg").is_file():
        return None
    return environment


#: Extras a rebuild never carries from a generation or an earlier release:
#: ``matrix`` builds python-olm from source, so a failed build would repeat on
#: every update; the umbrella extras would install far more than the operator
#: ever asked for (``[all]`` alone is most of upstream's optional set).
NEVER_CARRY = frozenset({"matrix", "all", "termux", "termux-all"})


def generation_extras(hermes_home: Path, src: Path) -> tuple[str, ...]:
    """The extras upstream recorded for the committed generation of ``src``.

    ``facts.json`` stores the selection as ``packages.venv.extras``
    (``pm/lock.py:269-279``): every extra a lazy install asked for, platform
    or not (a TTS engine, a search provider). :data:`NEVER_CARRY` is dropped.
    ``()`` when no generation is committed.
    """
    import json

    if committed_generation(hermes_home, src) is None:
        return ()
    state = Path(hermes_home) / "installs" / install_key(src)
    try:
        data = json.loads((state / "facts.json").read_text(encoding="utf-8-sig"))
        extras = data["packages"]["venv"]["extras"]
    except (OSError, ValueError, KeyError, TypeError):
        return ()
    if not isinstance(extras, list):
        return ()
    return tuple(sorted({e for e in extras if isinstance(e, str) and e not in NEVER_CARRY}))
