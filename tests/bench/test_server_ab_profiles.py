"""server_ab arms run under temporary profiles and always unwind (ADR-0029).

A tune lives on a profile, never on the slot: each arm POSTs a custom profile
cloned from the slot's current one, points the slot at it (``profile`` key
only), restarts and measures; ``finally`` points the slot back and deletes the
temporary profiles. ``server_ab`` is a stdlib-only script, so it is loaded by
path and its HTTP seam (``_http``) is replaced by an in-memory hal0-api.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
from typing import Any

import pytest

_PATH = Path(__file__).resolve().parents[2] / "installer" / "bench" / "server_ab.py"
_spec = importlib.util.spec_from_file_location("server_ab_profiles_under_test", _PATH)
assert _spec and _spec.loader
server_ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server_ab)


class FakeApi:
    """The slice of hal0-api server_ab talks to, with the real wire shapes."""

    def __init__(self, *, slot_profile: str = "agent") -> None:
        self.slot = {"name": "gpu-a", "port": 8081, "type": "llm", "profile": slot_profile}
        self.profiles: dict[str, dict[str, Any]] = {
            "agent": {
                "name": "agent",
                "flags": "-fa on -ctk q8_0",
                "ctx_size": 65536,
                "enable_thinking": True,
                "vision": "off",
                "speculation": "auto",
                "runner": None,
            },
            "chat": {
                "name": "chat",
                "flags": "-b 2048",
                "ctx_size": None,
                "enable_thinking": None,
                "vision": "auto",
                "speculation": "auto",
                "runner": None,
            },
        }
        self.calls: list[tuple[str, str, Any]] = []
        self.profile_at_restart: list[str] = []
        self.fail_delete: set[str] = set()
        self.fail_slot_put: bool = False

    def __call__(
        self, method: str, url: str, body: dict | None = None, timeout: float = 600.0, auth=None
    ) -> Any:
        path = url.split("127.0.0.1:8080", 1)[1]
        self.calls.append((method, path, body))
        if (method, path) == ("GET", "/api/slots"):
            return [dict(self.slot)]
        if (method, path) == ("GET", "/api/profiles"):
            return [
                {**p, "used_by": [self.slot["name"]] if p["name"] == self.slot["profile"] else []}
                for p in self.profiles.values()
            ]
        if method == "GET" and path.startswith("/api/profiles/"):
            return dict(self.profiles[path.rsplit("/", 1)[1]])
        if (method, path) == ("POST", "/api/profiles"):
            assert body is not None
            self.profiles[body["name"]] = dict(body)
            return dict(body)
        if method == "DELETE" and path.startswith("/api/profiles/"):
            name = path.rsplit("/", 1)[1]
            if name in self.fail_delete:
                raise OSError("delete refused")
            if name == self.slot["profile"]:
                raise OSError("profile in use")  # the API's profiles.in_use 409
            del self.profiles[name]
            return {}
        if method == "PUT" and path == "/api/slots/gpu-a/config":
            assert body is not None
            if self.fail_slot_put and body["profile"] == "agent":
                raise OSError("restore refused")
            self.slot["profile"] = body["profile"]
            return {}
        if (method, path) == ("POST", "/api/slots/gpu-a/restart"):
            self.profile_at_restart.append(self.slot["profile"])
            return {}
        if (method, path) == ("GET", "/api/slots/gpu-a/resolved"):
            return {
                "argv": ["ghcr.io/x/runner:1", "llama-server", "--ctx-size", "65536"],
                "provenance": [],
                "removed": 0,
            }
        raise AssertionError(f"unexpected call {method} {path}")

    def posted_profiles(self) -> list[dict[str, Any]]:
        return [b for m, p, b in self.calls if (m, p) == ("POST", "/api/profiles") and b]

    def slot_profile_writes(self) -> list[str]:
        return [
            b["profile"] for m, p, b in self.calls if (m, p) == ("PUT", "/api/slots/gpu-a/config")
        ]

    def slot_config_bodies(self) -> list[dict]:
        return [b for m, p, b in self.calls if (m, p) == ("PUT", "/api/slots/gpu-a/config") and b]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    fake = FakeApi()
    monkeypatch.setattr(server_ab, "_http", fake)
    monkeypatch.setattr(server_ab, "_wait_ready", lambda port, timeout_s=300.0: None)
    monkeypatch.setattr(
        server_ab,
        "_completion",
        lambda port, prompt, n, **kw: {"predicted_per_second": 50.0, "prompt_per_second": 500.0},
    )
    return fake


def _args(**kw: Any) -> argparse.Namespace:
    base = {
        "api": "http://127.0.0.1:8080",
        "slot": "gpu-a",
        "variant": [],
        "n": 2,
        "max_tokens": 8,
        "ctx_size": None,
        "mtp": None,
        "np": "1,2",
        "concurrency": 0,
        "depth": 100,
        "temp": 0.0,
        "top_p": None,
        "top_k": None,
        "spec_nmax": "2",
        "spec_pmin": "0.5",
        "spec_nmin": 0,
        "per_request": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _assert_unwound(api: FakeApi, original: str = "agent") -> None:
    assert api.slot["profile"] == original
    assert set(api.profiles) == {"agent", "chat"}, "temporary profiles must be deleted"


# ── ab ───────────────────────────────────────────────────────────────────────


def test_ab_clones_the_slot_profile_per_arm_and_unwinds(api: FakeApi) -> None:
    results = server_ab.mode_ab(
        _args(variant=["base:", "ub;ctx=8192;mtp=off:-ub 256 -b 1024"], mtp=None)
    )

    first, second = api.posted_profiles()
    assert first["name"].startswith("tune-gpu-a-")
    assert first["flags"] == "-fa on -ctk q8_0"  # the original flags, no extras
    assert first["ctx_size"] == 65536  # inherited from the cloned profile
    assert first["cloned_from"] == "agent"
    assert first["vision"] == "off" and first["enable_thinking"] is True
    # The arm's flags come AFTER the originals so last-wins lets them override.
    assert second["flags"] == "-fa on -ctk q8_0 -ub 256 -b 1024"
    assert second["ctx_size"] == 8192
    assert second["speculation"] == "off"

    assert results["base"]["extra_args"] == "-fa on -ctk q8_0"
    assert results["ub"]["ctx_size"] == 8192 and results["ub"]["mtp"] == "off"

    # Each arm was measured under its own profile; then the original came back.
    assert api.slot_profile_writes() == [first["name"], second["name"], "agent"]
    assert api.profile_at_restart == [first["name"], second["name"], "agent"]
    _assert_unwound(api)


def test_ab_slot_config_writes_carry_the_profile_key_only(api: FakeApi) -> None:
    server_ab.mode_ab(_args(variant=["x:-b 512"]))

    assert api.slot_config_bodies()
    assert all(set(body) == {"profile"} for body in api.slot_config_bodies())
    flat = repr(api.calls)
    assert "extra_args" not in flat.replace("'extra_args'", "")  # nothing writes the slot tune
    assert "parallel" not in repr(api.slot_config_bodies())


def test_run_wide_ctx_and_mtp_apply_to_every_arm_and_a_variant_overrides(api: FakeApi) -> None:
    server_ab.mode_ab(_args(variant=["a:", "b;ctx=4096:"], ctx_size=16384, mtp="off"))

    a, b = api.posted_profiles()
    assert (a["ctx_size"], a["speculation"]) == (16384, "off")
    assert (b["ctx_size"], b["speculation"]) == (4096, "off")


def test_ab_unwinds_when_a_measurement_fails(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> dict:
        raise RuntimeError("completion died")

    monkeypatch.setattr(server_ab, "_completion", boom)

    with pytest.raises(RuntimeError, match="completion died"):
        server_ab.mode_ab(_args(variant=["a:-b 512", "b:-b 1024"]))

    _assert_unwound(api)
    assert api.profile_at_restart[-1] == "agent"


def test_ab_unwinds_on_ctrl_c(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        server_ab, "_wait_ready", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt())
    )

    with pytest.raises(KeyboardInterrupt):
        server_ab.mode_ab(_args(variant=["a:-b 512"]))

    _assert_unwound(api)


def test_a_slot_with_no_profile_clones_its_type_seed_and_is_restored_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeApi(slot_profile="")
    monkeypatch.setattr(server_ab, "_http", fake)
    monkeypatch.setattr(server_ab, "_wait_ready", lambda *a, **k: None)
    monkeypatch.setattr(server_ab, "_completion", lambda *a, **k: {})

    server_ab.mode_ab(_args(variant=["a:-ub 128"]))

    (posted,) = fake.posted_profiles()
    assert posted["flags"] == "-b 2048 -ub 128"  # cloned from the chat seed
    assert posted["cloned_from"] == "chat"
    assert fake.slot["profile"] == ""
    assert set(fake.profiles) == {"agent", "chat"}


def test_cleanup_keeps_going_and_warns_when_a_step_fails(
    api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.fail_slot_put = True  # the restore PUT is refused

    server_ab.mode_ab(_args(variant=["a:-b 512"]))

    err = capsys.readouterr().err
    assert "cleanup incomplete" in err
    assert "could not restore profile 'agent'" in err
    # The delete was still attempted (it fails here because the slot still uses it).
    assert any(m == "DELETE" for m, _p, _b in api.calls)


def test_cleanup_reports_every_failed_delete(
    api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.fail_delete = {f"tune-gpu-a-{os.getpid() % 100000}-1"}

    server_ab.mode_ab(_args(variant=["a:-b 512", "b:-b 1024"]))

    err = capsys.readouterr().err
    assert "could not delete temporary profile" in err
    assert api.slot["profile"] == "agent"
    assert len(api.profiles) == 3  # only the refused one is left behind, and it was reported


# ── reuse / batch / mtp ──────────────────────────────────────────────────────


def test_reuse_runs_both_arms_as_profiles_and_unwinds(api: FakeApi) -> None:
    results = server_ab.mode_reuse(_args())

    flags = [p["flags"] for p in api.posted_profiles()]
    assert flags == ["-fa on -ctk q8_0 --cache-reuse 256", "-fa on -ctk q8_0 --cache-reuse 0"]
    assert set(results) == {"cache-reuse-256", "cache-reuse-0"}
    _assert_unwound(api)


def test_batch_sets_parallel_through_profile_flags_not_the_slot(
    api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        server_ab,
        "_one_stream",
        lambda port, prompt, n: {"predicted_n": n, "predicted_per_second": 20.0, "prompt_ms": 10},
    )

    results = server_ab.mode_batch(_args(np="1,4", concurrency=2, n=1))

    assert [p["flags"] for p in api.posted_profiles()] == [
        "-fa on -ctk q8_0 --parallel 1",
        "-fa on -ctk q8_0 --parallel 4",
    ]
    assert set(results) == {"np1", "np4"}
    assert results["np4"]["parallel"] == 4
    assert all(set(b) == {"profile"} for b in api.slot_config_bodies())
    _assert_unwound(api)


def test_mtp_sweep_relaunches_per_cell_with_draft_flags(api: FakeApi) -> None:
    server_ab.mode_mtp(_args(spec_nmax="2,3", spec_pmin="0.5"))

    flags = [p["flags"] for p in api.posted_profiles()]
    assert flags == [
        "-fa on -ctk q8_0 --spec-draft-n-max 2 --spec-draft-n-min 0 --spec-draft-p-min 0.5",
        "-fa on -ctk q8_0 --spec-draft-n-max 3 --spec-draft-n-min 0 --spec-draft-p-min 0.5",
    ]
    _assert_unwound(api)


def test_mtp_per_request_touches_no_profile(api: FakeApi) -> None:
    server_ab.mode_mtp(_args(per_request=True))

    assert api.posted_profiles() == []
    assert api.slot_profile_writes() == []
    assert not any(m == "DELETE" for m, _p, _b in api.calls)


# ── provenance + helpers ─────────────────────────────────────────────────────


def test_provenance_reads_the_resolved_command_not_llamacpp_args(api: FakeApi) -> None:
    prov = server_ab._provenance(_args(runner_image=None, decode_tune=None, note=None), api.slot)

    assert prov["slot_resolved_args"] == "llama-server --ctx-size 65536"  # image dropped
    assert prov["slot_profile"] == "agent"
    assert ("GET", "/api/slots/gpu-a/resolved", None) in api.calls


def test_resolved_command_is_none_when_the_route_has_no_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_ab, "_http", lambda *a, **k: {"name": "x", "argv": None})
    assert server_ab._resolved_command("http://127.0.0.1:8080", "x") is None


def test_parse_variant() -> None:
    assert server_ab._parse_variant("a:-b 512") == ("a", "-b 512", None, None)
    assert server_ab._parse_variant("big;ctx=32768;mtp=off:-fa on") == (
        "big",
        "-fa on",
        32768,
        "off",
    )
    assert server_ab._parse_variant("-b 512") == ("-b 512", "", None, None)  # bare label
    assert server_ab._parse_variant(":-b 1") == ("-b 1", "-b 1", None, None)


def test_parse_variant_rejects_an_unknown_option() -> None:
    with pytest.raises(SystemExit):
        server_ab._parse_variant("a;speed=fast:-b 1")


# ── startup sweep of arm profiles a hard-killed run left behind ──────────────

_ARM = "temporary server_ab arm for slot gpu-a"


def _leftover(api: FakeApi, name: str, *, intent: str = _ARM) -> None:
    api.profiles[name] = {"name": name, "flags": "-b 1", "intent": intent, "vision": "auto"}


def test_sweep_deletes_unreferenced_arm_profiles_and_logs_each(
    api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _leftover(api, "tune-gpu-a-111-1")
    _leftover(api, "tune-gpu-a-111-2")

    deleted = server_ab.sweep_stale_arm_profiles("http://127.0.0.1:8080", "gpu-a")

    assert sorted(deleted) == ["tune-gpu-a-111-1", "tune-gpu-a-111-2"]
    assert set(api.profiles) == {"agent", "chat"}
    out = capsys.readouterr().out
    assert "removed stale temporary profile 'tune-gpu-a-111-1'" in out
    assert "removed stale temporary profile 'tune-gpu-a-111-2'" in out


def test_sweep_never_deletes_a_profile_a_slot_still_points_at(
    api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _leftover(api, "tune-gpu-a-111-1")
    _leftover(api, "tune-gpu-a-111-2")
    api.slot["profile"] = "tune-gpu-a-111-2"  # a killed run left the slot on its arm

    deleted = server_ab.sweep_stale_arm_profiles("http://127.0.0.1:8080", "gpu-a")

    assert deleted == ["tune-gpu-a-111-1"]
    assert "tune-gpu-a-111-2" in api.profiles
    assert not any((m, p) == ("DELETE", "/api/profiles/tune-gpu-a-111-2") for m, p, _b in api.calls)
    captured = capsys.readouterr()
    assert "tune-gpu-a-111-2" in captured.err
    assert "still used by slot(s) gpu-a" in captured.err
    assert "not deleting" in captured.err


def test_sweep_leaves_other_slots_operator_profiles_and_lookalikes_alone(api: FakeApi) -> None:
    _leftover(api, "tune-other-111-1")  # another slot's arm
    _leftover(api, "tune-gpu-a-mine", intent="my own tune")  # operator profile, same prefix
    _leftover(api, "tune-gpu-a-blank", intent="")
    _leftover(api, "gpu-a-tune-1")  # not the arm naming scheme

    deleted = server_ab.sweep_stale_arm_profiles("http://127.0.0.1:8080", "gpu-a")

    assert deleted == []
    assert {"tune-other-111-1", "tune-gpu-a-mine", "tune-gpu-a-blank", "gpu-a-tune-1"} <= set(
        api.profiles
    )
    assert not any(m == "DELETE" for m, _p, _b in api.calls)


def test_sweep_skips_quietly_when_the_api_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def down(*_a: Any, **_k: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(server_ab, "_http", down)

    assert server_ab.sweep_stale_arm_profiles("http://127.0.0.1:8080", "gpu-a") == []
    assert "sweep skipped" in capsys.readouterr().err


def test_a_failed_delete_is_reported_and_the_sweep_continues(
    api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _leftover(api, "tune-gpu-a-111-1")
    _leftover(api, "tune-gpu-a-111-2")
    api.fail_delete = {"tune-gpu-a-111-1"}

    deleted = server_ab.sweep_stale_arm_profiles("http://127.0.0.1:8080", "gpu-a")

    assert deleted == ["tune-gpu-a-111-2"]
    assert "could not delete stale profile 'tune-gpu-a-111-1'" in capsys.readouterr().err


def test_arms_carry_the_marker_the_sweep_looks_for(api: FakeApi) -> None:
    server_ab.mode_ab(_args(variant=["a:-b 512"]))

    (posted,) = api.posted_profiles()
    assert posted["intent"] == "temporary server_ab arm for slot gpu-a"
    assert posted["name"].startswith(server_ab._arm_prefix("gpu-a"))


def test_main_sweeps_before_it_measures(
    api: FakeApi, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _leftover(api, "tune-gpu-a-999-1")
    out = tmp_path / "ab.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "server_ab.py",
            "--mode",
            "ab",
            "--slot",
            "gpu-a",
            "--variant",
            "a:-b 512",
            "--n",
            "1",
            "--out",
            str(out),
        ],
    )

    server_ab.main()

    methods = [(m, p) for m, p, _b in api.calls]
    swept = methods.index(("DELETE", "/api/profiles/tune-gpu-a-999-1"))
    first_arm = methods.index(("POST", "/api/profiles"))
    assert swept < first_arm
    assert out.exists()
    _assert_unwound(api)


def test_sweep_tells_apart_two_slots_sharing_their_first_12_characters(
    api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    """Arm names truncate the slot to 12 chars, so these two slots share a name
    prefix; only the intent (full slot name) says whose arm a profile is."""
    long_a = "gpu-cluster-a"  # -> tune-gpu-cluster--...
    long_b = "gpu-cluster-b"
    assert long_a[:12] == long_b[:12]
    assert server_ab._arm_prefix(long_a) == server_ab._arm_prefix(long_b)
    _leftover(api, f"{server_ab._arm_prefix(long_a)}111-1", intent=server_ab._arm_intent(long_a))
    _leftover(api, f"{server_ab._arm_prefix(long_b)}222-1", intent=server_ab._arm_intent(long_b))

    deleted = server_ab.sweep_stale_arm_profiles("http://127.0.0.1:8080", long_a)

    assert deleted == [f"{server_ab._arm_prefix(long_a)}111-1"]
    assert f"{server_ab._arm_prefix(long_b)}222-1" in api.profiles  # the sibling's arm survives
    assert "removed stale temporary profile" in capsys.readouterr().out

    # ...and the sibling's own sweep removes its arm, not a stranger's.
    assert server_ab.sweep_stale_arm_profiles("http://127.0.0.1:8080", long_b) == [
        f"{server_ab._arm_prefix(long_b)}222-1"
    ]


def test_an_arm_for_another_slot_with_the_same_prefix_is_not_swept_even_unreferenced(
    api: FakeApi,
) -> None:
    long_b = "gpu-cluster-b"
    _leftover(api, f"{server_ab._arm_prefix(long_b)}7-1", intent=server_ab._arm_intent(long_b))

    assert server_ab.sweep_stale_arm_profiles("http://127.0.0.1:8080", "gpu-cluster-a") == []
    assert not any(m == "DELETE" for m, _p, _b in api.calls)
