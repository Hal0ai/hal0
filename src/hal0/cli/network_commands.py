"""hal0 network subcommands — the persisted browser-origin additions (#2382).

``HAL0_ALLOWED_ORIGINS`` lives in the installer-managed block of
``/etc/hal0/api.env``, which every installer run rewrites, so an origin
added there by hand was lost on the next re-run. These verbs edit
``hal0.toml``'s ``[network]`` section instead. The origin gates merge it
into the allowlist on the next request (no restart).

Endpoints hit
-------------

    hal0 network public-url show             → GET /api/settings
    hal0 network public-url set <url>        → PUT /api/settings {"network": {"public_url": url}}
    hal0 network public-url clear            → PUT /api/settings {"network": {"public_url": null}}
    hal0 network origins list                → GET /api/settings
    hal0 network origins add <origin>        → POST /api/settings/network {"add_origins": [origin]}
    hal0 network origins remove <origin>     → POST /api/settings/network {"remove_origins": [origin]}

Writes go through the locked hal0.toml transaction the Settings page uses,
never ``api.env``. ``origins add|remove`` send one entry and the server
applies it to the list on disk inside that transaction, so a stale read can
never write back an origin removed elsewhere. Values are validated locally
first (``hal0.config.schema.NetworkConfig``) for a clear error, and again
by the server.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console

from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_get,
    api_post,
    api_put,
    die,
)
from hal0.config.schema import NetworkConfig, normalise_origin
from hal0.install.network import _origin_of

app = typer.Typer(help="Browser origins hal0 accepts: reverse-proxy URL and extra origins.")
public_url_app = typer.Typer(help="The URL users reach hal0 at through a reverse proxy.")
origins_app = typer.Typer(help="Extra browser origins allowed on top of the installer's list.")
app.add_typer(public_url_app, name="public-url")
app.add_typer(origins_app, name="origins")
# Plain output: "[network]" is text here, not rich markup.
console = Console(markup=False, highlight=False, soft_wrap=True)

_APPLIES = "applies on the next request, no restart"


def _require_api() -> None:
    if _api_unreachable(_api_base()):
        raise typer.Exit(1)


def _current_network() -> dict[str, Any]:
    """The live ``[network]`` section, as ``GET /api/settings`` reports it."""
    try:
        cfg = api_get("/api/settings")
    except CliApiError as exc:
        die(str(exc))
        raise  # unreachable; die() exits
    section = cfg.get("network") if isinstance(cfg, dict) else None
    return section if isinstance(section, dict) else {}


def _put_network(patch: dict[str, Any]) -> dict[str, Any]:
    try:
        result = api_put("/api/settings", json={"network": patch})
    except CliApiError as exc:
        die(str(exc))
        raise
    section = result.get("network") if isinstance(result, dict) else None
    return section if isinstance(section, dict) else {}


def _origin_or_die(value: str) -> str:
    try:
        return normalise_origin(value)
    except ValueError as exc:
        die(str(exc))
        raise


@public_url_app.command("show")
def public_url_show() -> None:
    """Print the persisted [network].public_url (empty when unset)."""
    _require_api()
    value = _current_network().get("public_url")
    console.print(value or "(unset)")


@public_url_app.command("set")
def public_url_set(
    url: str = typer.Argument(..., help="Absolute http(s) URL, e.g. https://hal0.example.com"),
) -> None:
    """Set [network].public_url; its origin is added to the allowlist."""
    try:
        normalised = NetworkConfig(public_url=url).public_url
    except ValueError as exc:
        die(f"invalid public_url: {exc}")
        return
    if normalised is None:
        die("public_url must not be empty; use `hal0 network public-url clear` to unset it")
        return
    _require_api()
    section = _put_network({"public_url": normalised})
    console.print(f"[network].public_url = {section.get('public_url', normalised)} ({_APPLIES}).")


@public_url_app.command("clear")
def public_url_clear() -> None:
    """Unset [network].public_url."""
    _require_api()
    _put_network({"public_url": None})
    console.print(f"[network].public_url cleared ({_APPLIES}).")


@origins_app.command("list")
def origins_list() -> None:
    """List the origins [network] adds to the allowlist."""
    _require_api()
    section = _current_network()
    public = section.get("public_url")
    extra = section.get("extra_origins") or []
    if not public and not extra:
        console.print("No [network] origins set. HAL0_ALLOWED_ORIGINS still applies.")
        return
    if public:
        console.print(f"{_origin_of(public)}  (from public_url)")
    for origin in extra:
        console.print(origin)


def _edit_origins(body: dict[str, list[str]]) -> bool:
    """Apply one add/remove server-side, inside the config transaction.

    Returns whether ``extra_origins`` changed. The server edits the list it
    reads from disk under the lock, so a concurrent add or remove elsewhere
    is never lost (a GET-then-PUT of the whole list would race).
    """
    try:
        result = api_post("/api/settings/network", json=body)
    except CliApiError as exc:
        die(str(exc))
        raise
    meta = result.get("_hal0") if isinstance(result, dict) else None
    changeset = meta.get("changeset") if isinstance(meta, dict) else None
    changes = changeset.get("changes") if isinstance(changeset, dict) else None
    return bool(changes)


@origins_app.command("add")
def origins_add(
    origin: str = typer.Argument(..., help="scheme://host[:port], e.g. http://box.lan:8080"),
) -> None:
    """Add an origin to [network].extra_origins."""
    wanted = _origin_or_die(origin)
    _require_api()
    if not _edit_origins({"add_origins": [wanted]}):
        console.print(f"{wanted} is already in [network].extra_origins.")
        return
    console.print(f"Added {wanted} to [network].extra_origins ({_APPLIES}).")


@origins_app.command("remove")
def origins_remove(
    origin: str = typer.Argument(..., help="An origin previously added with `origins add`."),
) -> None:
    """Remove an origin from [network].extra_origins."""
    wanted = _origin_or_die(origin)
    _require_api()
    if not _edit_origins({"remove_origins": [wanted]}):
        console.print(f"{wanted} is not in [network].extra_origins; nothing to remove.")
        return
    console.print(f"Removed {wanted} from [network].extra_origins ({_APPLIES}).")
