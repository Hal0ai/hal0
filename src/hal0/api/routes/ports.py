"""GET /api/ports — the global port-claim map (hal0.ports registry).

One place to answer "who owns which port": every slot config claim
(including disabled slots), every runtime slot row (FLM-trio virtual
ports), reserved ports, and live listeners inside the pool — plus the
conflicts among them and the next free port auto-assign would pick.
Surfaced to agents as the ``port_list`` admin tool.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("")
async def list_ports(request: Request) -> dict[str, object]:
    from hal0.api.routes.slots import _get_slot_manager, _slot_port_range
    from hal0.config.paths import slots_config_dir
    from hal0.ports import port_report

    snapshots: list[dict] = []
    try:
        sm = _get_slot_manager(request)
    except Exception:
        sm = None
    if sm is not None:
        try:
            snapshots = [
                {
                    "name": getattr(s, "name", None),
                    "port": getattr(s, "port", None),
                    "coresident_group": getattr(s, "coresident_group", None),
                }
                for s in await sm.list()
            ]
        except Exception:
            snapshots = []

    return port_report(
        slots_dir=slots_config_dir(),
        pool=_slot_port_range(),
        slot_snapshots=snapshots,
        reserved={8080: "api"},
    )
