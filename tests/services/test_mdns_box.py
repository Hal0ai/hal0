"""mDNS reports honestly inside the podman box, and ``in_box`` ignores LXC."""

from __future__ import annotations

from pathlib import Path

from hal0.services import mdns
from hal0.system import box


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


async def test_mdns_unavailable_in_box(monkeypatch) -> None:
    monkeypatch.setattr(box, "in_box", lambda *a: True)

    async def _active(_unit: str) -> bool:
        raise AssertionError("must not claim availability from avahi in the box")

    monkeypatch.setattr(mdns.systemd, "unit_is_active", _active)
    status = await mdns.status()

    assert status["available"] is False
    assert "box" in str(status["reason"])
    assert status["advertised"] == []


async def test_mdns_outside_box_has_no_reason(monkeypatch) -> None:
    monkeypatch.setattr(box, "in_box", lambda *a: False)

    async def _active(_unit: str) -> bool:
        return True

    monkeypatch.setattr(mdns.systemd, "unit_is_active", _active)
    status = await mdns.status()

    assert status["available"] is True
    assert "reason" not in status


def test_in_box_markers(tmp_path: Path) -> None:
    assert box.in_box(tmp_path) is False
    _write(tmp_path, "run/.containerenv", "")
    assert box.in_box(tmp_path) is True


def test_in_box_systemd_marker_podman(tmp_path: Path) -> None:
    _write(tmp_path, "run/systemd/container", "podman\n")
    assert box.in_box(tmp_path) is True


def test_in_box_not_fooled_by_lxc(tmp_path: Path) -> None:
    _write(tmp_path, "run/systemd/container", "lxc\n")
    assert box.in_box(tmp_path) is False
