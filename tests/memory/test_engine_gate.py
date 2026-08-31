"""Hindsight pin-bump evidence gate (spec §5). HAL0_ENGINE_GATE=1 only.

Exercises upgrade_memory_engine against REAL engines: old pin installed
fresh, seeded over HTTP, then converged to HINDSIGHT_API_PIN with a
process-seam (no systemd in CI — svc stop/start map to killing/spawning
the venv's own hindsight-api). Asserts version, data survival, rollback,
and the client-call contract.

Three jobs, in the order the module runs them (test order is execution
order and matters here — the engine binds ONE loopback port, 9177, so at
most one engine process may live at a time):

1. ``test_upgrade_path_preserves_data`` — old pin → pin, data survives.
2. ``test_client_contract_against_new_engine`` — HindsightRestClient's core
   surface still answers on the NEW engine (catches removed/renamed
   endpoints). Runs against the engine test 1 left upgraded and healthy.
3. ``test_rollback_restores_old_engine`` — a forced postcheck failure must
   restore both the old venv and the ``.pg0`` snapshot. Last, because it
   stops the module engine and stands up a second one on the same port.

Deviations from the design sketch, each against an authority in-tree:

* REST paths/payloads come from ``hal0.memory.hindsight_client`` — the bank
  surface is ``/v1/default/banks/{bank}/…`` (``retain`` at :130, ``recall``
  at :177), NOT the bank-less ``/v1/default/memories``. Every call carries
  the ``Authorization: Bearer`` header the client always sends (the engine
  requires a non-empty key even with auth off).
* The engine child process inherits the environment
  ``installer/systemd/hindsight-api.service`` pins — ``HOME`` (embedded
  postgres lives at ``$HOME/.pg0``), ``HF_HOME`` (writable embedder cache),
  ``HINDSIGHT_API_SKIP_LLM_VERIFICATION`` (no LLM in CI; without it startup
  blocks until the verification timeout and never binds) and the two
  ``*_FORCE_CPU`` flags (no CUDA on a runner).
* Data survival is asserted on **bank config + directives**, not on retained
  documents or recall hits. Retain does not write a durable document row
  synchronously: it queues an operation whose worker persists nothing until
  LLM fact extraction succeeds. The gate has no LLM (see ``_engine_env``), so
  a seeded document never lands — measured against hindsight-api 0.8.4, the
  bank reports ``total_documents: 0`` and the operation carries
  ``"Fact extraction failed: 1/1 chunks failed. First failures: chunk 0:
  APIConnectionError"``. Bank config (``PUT /banks/{id}``, the installer's
  seeding path) and directives (``POST /banks/{id}/directives``,
  ``HindsightRestClient.create_directive``) are operator-authored rows
  written synchronously with no LLM in the path — durable content that the
  one-way alembic migration must carry across, and therefore the honest
  survival evidence. Retain and recall are still called, for their contract.
"""

from __future__ import annotations

import json as jsonlib
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from hal0.memory import engine_upgrade
from hal0.memory.engine_upgrade import (
    ENGINE_BASE_URL,
    HINDSIGHT_API_PIN,
    PY_MAX_MINOR,
    PY_MIN_MINOR,
    upgrade_memory_engine,
)
from hal0.memory.hindsight_client import DEFAULT_API_KEY, HindsightRestClient

pytestmark = pytest.mark.engine_gate

if os.environ.get("HAL0_ENGINE_GATE") != "1":
    pytest.skip("engine gate: set HAL0_ENGINE_GATE=1", allow_module_level=True)

OLD_PIN = os.environ["HAL0_GATE_OLD_PIN"]

#: Interpreter the throwaway engine venvs are built from. Defaults to the one
#: running pytest (CI pins it via setup-python); ``HAL0_GATE_PYTHON`` overrides
#: it on a dev box whose default python sits outside the engine's supported
#: band. Checked against engine_upgrade's own band constants rather than a
#: second copy of the numbers.
GATE_PYTHON = os.environ.get("HAL0_GATE_PYTHON") or sys.executable

#: The global cross-agent bank (``namespace.DEFAULT_DATASET``). install.sh
#: seeds it, but banks also lazy-create on first write, so the gate just
#: writes to it.
BANK = "shared"
SEED_MISSION = "gate-seed retain mission marker"
SEED_DIRECTIVE = "hal0-engine-gate-directive"
SEED_DIRECTIVE_TEXT = "gate-seed durable directive"
SEED_DOC_ID = "hal0-engine-gate-seed"
SEED_TEXT = "gate-seed shared fact"

_AUTH = {"Authorization": f"Bearer {DEFAULT_API_KEY}", "Content-Type": "application/json"}


def _engine_env(hs_dir: Path) -> dict[str, str]:
    """The subset of hindsight-api.service's Environment= lines CI needs.

    The four ``*_LLM_*`` values are the unit's literals. They are not
    optional: ``MemoryEngine.__init__`` raises ``ValueError("LLM API key is
    required…")`` and the process dies before binding :9177 without a
    NON-EMPTY key (the unit calls this out as "spike gotcha #1"). Nothing
    listens on the unit's ``127.0.0.1:8080`` LLM base URL under the gate, so
    extraction/reflect fail lazily exactly as they do on a fresh box with no
    utility model loaded — which is why the gate asserts on retained
    documents rather than on extracted/recalled content.
    """
    return {
        **os.environ,
        "HOME": str(hs_dir),
        "HF_HOME": str(hs_dir / "hf-cache"),
        "HINDSIGHT_API_LLM_PROVIDER": "openai",
        "HINDSIGHT_API_LLM_BASE_URL": "http://127.0.0.1:8080/v1",
        "HINDSIGHT_API_LLM_MODEL": "hal0/utility",
        "HINDSIGHT_API_LLM_API_KEY": DEFAULT_API_KEY,
        "HINDSIGHT_API_SKIP_LLM_VERIFICATION": "true",
        "HINDSIGHT_API_EMBEDDINGS_LOCAL_FORCE_CPU": "true",
        "HINDSIGHT_API_RERANKER_LOCAL_FORCE_CPU": "true",
    }


class EngineProc:
    """Process-seam standing in for systemd: one engine process per venv."""

    def __init__(self, hs_dir: Path) -> None:
        self.hs_dir = hs_dir
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        binary = self.hs_dir / ".venv" / "bin" / "hindsight-api"
        self.proc = subprocess.Popen(
            [str(binary), "--host", "127.0.0.1", "--port", "9177"],
            cwd=self.hs_dir,
            env=_engine_env(self.hs_dir),
        )

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def seam(self):
        """SystemCtlSeam stand-in matching engine_upgrade._svc's call shape."""
        outer = self

        class _Seam:
            def systemctl(self, _bin, verb, _unit, *, check=True, timeout=0.0):
                if verb == "stop":
                    outer.stop()
                elif verb == "start":
                    outer.start()

        return _Seam()


def _wait_health(total_s: float = 240.0) -> None:
    deadline = time.monotonic() + total_s
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{ENGINE_BASE_URL}/health", timeout=3)
            return
        except OSError:
            time.sleep(2)
    raise AssertionError("engine never became healthy")


def _http(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{ENGINE_BASE_URL}{path}",
        method=method,
        data=jsonlib.dumps(body).encode() if body is not None else None,
        headers=_AUTH,
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return jsonlib.loads(resp.read().decode() or "{}")


def _seed() -> None:
    """Write the durable rows whose survival the gate asserts, then retain.

    All three writes go through the REST shapes hal0 itself uses. The first
    two land synchronously with no LLM involved and are what the survival
    assertions read back; the retain is fired for its call contract only (its
    document row needs extraction the gate cannot run — see module docstring).
    """
    # Creates the bank if absent — the installer's seeding path (install.sh
    # `_seed_bank`), and the only reason a fresh .pg0 has a `shared` bank.
    _http(
        "PUT",
        f"/v1/default/banks/{BANK}",
        {"retain_mission": SEED_MISSION, "disposition_skepticism": 4},
    )
    # HindsightRestClient.create_directive's exact payload (:394).
    _http(
        "POST",
        f"/v1/default/banks/{BANK}/directives",
        {
            "name": SEED_DIRECTIVE,
            "content": SEED_DIRECTIVE_TEXT,
            "priority": 7,
            "is_active": True,
            "tags": ["hal0-engine-gate"],
        },
    )
    # HindsightRestClient.retain's exact payload (:130).
    _http(
        "POST",
        f"/v1/default/banks/{BANK}/memories",
        {"items": [{"content": SEED_TEXT, "document_id": SEED_DOC_ID}], "async": True},
    )


def _assert_seed_survives(when: str) -> None:
    """The seeded bank config + directive must still be readable."""
    config = _http("GET", f"/v1/default/banks/{BANK}/config").get("config", {})
    assert config.get("retain_mission") == SEED_MISSION, f"{when}: bank config lost"

    directives = _http("GET", f"/v1/default/banks/{BANK}/directives").get("items", [])
    match = next((d for d in directives if d.get("name") == SEED_DIRECTIVE), None)
    assert match is not None, f"{when}: directive {SEED_DIRECTIVE!r} lost — {directives}"
    assert match.get("content") == SEED_DIRECTIVE_TEXT, f"{when}: directive body changed — {match}"
    assert match.get("priority") == 7, f"{when}: directive priority changed — {match}"
    assert match.get("tags") == ["hal0-engine-gate"], f"{when}: directive tags changed — {match}"


def _reported_version() -> str | None:
    return _http("GET", "/version").get("api_version")


def _build_engine_dir(root: Path) -> EngineProc:
    """Fresh venv at OLD_PIN under ``root``, started, healthy and seeded."""
    # Same predicate the pass itself uses to accept an interpreter, so the gate
    # can never build an engine venv the production path would have refused.
    if not engine_upgrade._interpreter_in_band(subprocess.run, GATE_PYTHON):
        pytest.fail(
            f"gate interpreter {GATE_PYTHON} is outside the engine's supported "
            f"3.{PY_MIN_MINOR}-3.{PY_MAX_MINOR} band; set HAL0_GATE_PYTHON"
        )
    subprocess.run([GATE_PYTHON, "-m", "venv", str(root / ".venv")], check=True)
    pip = str(root / ".venv" / "bin" / "pip")
    subprocess.run([pip, "install", "--upgrade", "pip", "wheel", "-q"], check=True, timeout=2400)
    subprocess.run(
        [pip, "install", f"hindsight-api=={OLD_PIN}", "-q"],
        check=True,
        timeout=2400,
    )
    proc = EngineProc(root)
    proc.start()
    _wait_health()
    # Seed via the same REST surface hal0.memory.hindsight_client wraps.
    _seed()
    _assert_seed_survives("seed")
    return proc


@pytest.fixture(scope="module")
def old_engine(tmp_path_factory):
    """Fresh venv at OLD_PIN with an initialized .pg0 + seeded memories."""
    hs = tmp_path_factory.mktemp("hindsight")
    proc = _build_engine_dir(hs)
    yield proc, hs
    proc.stop()


def test_upgrade_path_preserves_data(old_engine) -> None:
    proc, hs = old_engine
    result = upgrade_memory_engine(seam=proc.seam(), hs_dir=hs)
    assert result["status"] == "upgraded", result
    assert result["from"] == OLD_PIN, result
    assert result["to"] == HINDSIGHT_API_PIN, result
    _wait_health()
    assert _reported_version() == HINDSIGHT_API_PIN
    # The one-way alembic migration ran on the .pg0 that was snapshotted before
    # the swap — the seeded rows must have come through it.
    _assert_seed_survives("after upgrade")
    # Recall answers on the migrated schema (content is LLM-dependent, so this
    # asserts the call contract, not the ranking).
    recalled = _http(
        "POST",
        f"/v1/default/banks/{BANK}/memories/recall",
        {"query": "gate-seed", "max_tokens": 4096},
    )
    assert isinstance(recalled, dict), recalled
    # The rollback snapshot is kept, not pruned, on a successful upgrade.
    assert (hs / f".pg0.pre-{OLD_PIN}").is_dir()


async def test_client_contract_against_new_engine(old_engine) -> None:
    """Drive HindsightRestClient's core surface against the live new engine.

    Spec §5 job 3: a pin bump that removed or renamed an endpoint hal0 calls
    must fail here rather than in production. Each call raises
    ``httpx.HTTPStatusError`` on non-2xx (``raise_for_status`` inside the
    client), so reaching the assertion is itself the contract evidence.
    """
    _, _hs = old_engine
    assert _reported_version() == HINDSIGHT_API_PIN, "job 1 must have upgraded the engine"

    client = HindsightRestClient(base_url=ENGINE_BASE_URL)
    try:
        retained = await client.retain(
            bank_id=BANK,
            content="gate contract probe",
            document_id="hal0-engine-gate-contract",
        )
        assert isinstance(retained, dict), retained

        recalled = await client.recall(bank_id=BANK, query="gate-seed")
        assert isinstance(recalled, dict), recalled

        listed = await client.list_memories(bank_id=BANK, limit=10)
        assert isinstance(listed, dict | list), listed

        stats = await client.bank_stats(bank_id=BANK)
        assert isinstance(stats, dict), stats

        documents = await client.list_documents(bank_id=BANK, limit=100)
        assert isinstance(documents, dict | list), documents

        directives = await client.list_directives(bank_id=BANK)
        assert SEED_DIRECTIVE in jsonlib.dumps(directives)
    finally:
        await client.aclose()


def test_rollback_restores_old_engine(old_engine, tmp_path_factory) -> None:
    """A failing postcheck must restore BOTH the old venv and the .pg0 snapshot.

    Builds a SECOND old-pin engine dir (the first one is already converged),
    then forces the postcheck to fail by stubbing ``http_get`` to report a
    wrong ``/version`` while letting the real ``/health`` probe through — so
    the new engine genuinely starts and genuinely gets rolled back.
    """
    module_proc, _module_hs = old_engine
    # One loopback port: retire the converged engine before binding a second.
    module_proc.stop()

    hs = tmp_path_factory.mktemp("hindsight-rollback")
    proc = _build_engine_dir(hs)
    try:

        def http_get(url: str) -> dict | None:
            if url.endswith("/version"):
                # Real /health, lying /version: the pass sees a healthy engine
                # serving the wrong build — engine_upgrade.py:462's branch.
                return {"api_version": "0.0.0-not-the-pin"}
            return engine_upgrade._http_get_json(url)

        result = upgrade_memory_engine(seam=proc.seam(), hs_dir=hs, http_get=http_get)
        assert result["status"] == "rolled_back", result
        assert result["from"] == OLD_PIN, result
        assert "0.0.0-not-the-pin" in result["error"], result
        assert result["old_engine_healthy"] is True, result

        # Venv restored; the rejected build is kept for forensics.
        assert (hs / f".venv.failed-{HINDSIGHT_API_PIN}").is_dir()
        assert engine_upgrade._installed_version(subprocess.run, hs / ".venv") == OLD_PIN
        # Snapshot survives the restore, so a retry still has a rollback.
        assert (hs / f".pg0.pre-{OLD_PIN}").is_dir()

        # The old engine is back on the restored .pg0, with its data.
        _wait_health()
        assert _reported_version() == OLD_PIN
        _assert_seed_survives("after rollback")
    finally:
        proc.stop()
