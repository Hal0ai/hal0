"""``GET /api/runners/xdna/knobs`` — xdna knob schema for the dashboard (xdna-runner §3)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hal0.runners.knobs import XDNA_ENV_DOCS, XDNA_PMODES


def test_xdna_knobs_returns_pmodes_env_and_host_xrt(client: TestClient) -> None:
    r = client.get("/api/runners/xdna/knobs")
    assert r.status_code == 200
    body = r.json()

    assert body["pmodes"] == list(XDNA_PMODES)

    assert {e["name"] for e in body["env"]} == set(XDNA_ENV_DOCS)
    for entry in body["env"]:
        assert set(entry) == {"name", "default", "doc"}
        assert entry["doc"] == XDNA_ENV_DOCS[entry["name"]]

    assert isinstance(body["host_xrt"]["doc"], str)
    assert body["host_xrt"]["doc"]


def test_xdna_knobs_defaults_match_xdna_env_defaults(client: TestClient) -> None:
    from hal0.runners.knobs import XDNA_ENV_DEFAULTS

    body = client.get("/api/runners/xdna/knobs").json()
    by_name = {e["name"]: e["default"] for e in body["env"]}
    for name, default in XDNA_ENV_DEFAULTS.items():
        if name.startswith("GGML_XDNA_"):
            assert by_name[name] == default
