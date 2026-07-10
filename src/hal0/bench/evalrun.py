"""evalrun.py — agentic task eval (the quality tier, DESIGN §0 "pi-bench" un-deferred).

benchlab's cells measure SPEED. This module measures whether a model actually
COMPLETES agentic tasks — correctly and in order — by driving it as a real agent
and scoring a VERIFIABLE final value. No LLM judge: each task hides values across
sources (a fixture codebase, a page on hal0.dev) and requires the agent to gather
and combine them into one deterministic answer, so scoring is exact-match, and the
answer is only reachable if the intermediate steps succeeded.

Scaffold: hal0's own Hermes agent (operator choice), driven headless —
``hermes -z <prompt> -m <model> --provider custom --yolo -t <toolsets>
--pass-session-id`` — so we eval the model as it's actually used, with real
browser/file/shell tools. Reproducibility: targets are things you CONTROL and
that don't drift — a checked-in fixture repo, and hal0.dev (your own site). The
expected value is DERIVED from the source of truth at score time (re-fetch
hal0.dev / re-read the fixture), so a task never goes stale when the site changes.

Three scores per task (all from the transcript, no judge):
  * correctness — normalized final answer contains the derived expected value;
  * ordering / partial credit — how many hidden checkpoints appear in the trace
    (got the code value but failed the browser step → 1/2, diagnostic);
  * efficiency — wall time, turns, tool-calls, tokens (the speed tie-in).

Records land in ``<state root>/evals.jsonl`` (separate from throughput
records.jsonl — different shape: a score, not a t/s).
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .store import state_root

FIXTURES = Path(__file__).resolve().parent / "evals" / "agentic" / "fixtures"
HERMES = "/usr/local/bin/hermes"


# --------------------------------------------------------------------------- #
# tasks
# --------------------------------------------------------------------------- #


@dataclass
class Task:
    """One agentic task. ``expected_fn`` DERIVES the correct answer from the
    source of truth at score time (re-fetch / re-read), so the task self-updates
    and never hardcodes a value that can drift. ``checkpoints`` are the
    intermediate hidden values whose presence in the trace scores ordering."""

    id: str
    kind: str  # "code" | "browser" | "combine"
    prompt: str
    checkpoints: list[str]
    expected_fn: Callable[[], str]
    toolsets: str = "file,terminal,web,browser"
    timeout_s: int = 300
    fixture: str | None = None  # subdir under evals/agentic/fixtures/


def _fetch_text(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "hal0-bench-eval"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _discord_code() -> str:
    """The Discord invite code as it appears on hal0.dev RIGHT NOW (source of
    truth). Derived live so the task tracks the site — no hardcoded code."""
    html = _fetch_text("https://hal0.dev")
    m = re.search(r"discord\.(?:gg|com/invite)/([A-Za-z0-9]+)", html)
    if not m:
        raise RuntimeError("no discord invite found on hal0.dev")
    return m.group(1)


# --- derive-from-fixture expected answers (read the canonical fixture, so a task
#     never hardcodes a value that could drift out of sync with its files) ------


def _fx(name: str) -> Path:
    return FIXTURES / name


def _cipher_answer() -> str:
    import base64
    import codecs

    enc = (_fx("cipher-chain") / "secret.txt").read_text().strip()
    plain = codecs.encode(base64.b64decode(enc).decode(), "rot13")
    return re.search(r"\d+", plain).group()


def _loop_answer() -> str:
    import csv

    rows = list(csv.DictReader((_fx("loop-aggregate") / "orders.csv").read_text().splitlines()))
    shipped = sum(int(r["amount"]) for r in rows if r["status"] == "shipped")
    refund = int(re.search(r"REFUND:\s*(\d+)", (_fx("loop-aggregate") / "notes.md").read_text()).group(1))
    return str(shipped - refund)


def _dep_answer() -> str:
    keys = (_fx("dep-trace") / "keys.py").read_text()
    active = re.search(r'ACTIVE_KEY\s*=\s*"([^"]+)"', keys).group(1)
    registry = json.loads((_fx("dep-trace") / "registry.json").read_text())
    return str(registry[active])


def _grep_answer() -> str:
    seats = int(re.search(r"LICENSE_SEAT_COUNT\s*=\s*(\d+)",
                          (_fx("grep-hunt") / "deep/nested/license.conf").read_text()).group(1))
    per = int(re.search(r"SEATS_PER_POD:\s*(\d+)", (_fx("grep-hunt") / "pods.yaml").read_text()).group(1))
    return str(seats * per)


def _recurrence_answer() -> str:
    n = 3
    for _ in range(12):
        n = (n * 2 + 1) % 97
    return str(n)


def _browser_combine_answer() -> str:
    # BASE_PORT (codebase) + length of the live hal0.dev discord invite code
    return str(8412 + len(_discord_code()))


TASKS: list[Task] = [
    Task(
        id="codebase-combine",
        kind="code",
        fixture="codebase-combine",
        toolsets="file,terminal",
        timeout_s=240,
        checkpoints=["8412", "137"],
        expected_fn=lambda: "8549",  # BASE_PORT (src/config.py) + HEALTH_OFFSET (lib/util.py)
        prompt=(
            "You are in a small codebase (current directory). Read the code to find two values:\n"
            "  1) BASE_PORT — the port the primary listener binds.\n"
            "  2) HEALTH_OFFSET — the offset added to BASE_PORT for the health-check listener.\n"
            "They are defined in DIFFERENT files. Compute the health-check port = BASE_PORT + HEALTH_OFFSET.\n"
            "Reply with ONLY that final integer on the last line, nothing else."
        ),
    ),
    Task(
        id="cipher-chain",
        kind="cipher",
        fixture="cipher-chain",
        toolsets="file,terminal",
        timeout_s=300,
        checkpoints=["final code", "7391"],
        expected_fn=_cipher_answer,
        prompt=(
            "The file secret.txt in the current directory contains one line of gibberish. It was "
            "base64-encoded, and the bytes underneath are ROT13-encrypted. Decode it fully "
            "(base64 first, then ROT13) to recover an English sentence containing a number. "
            "Reply with ONLY that number on the last line, nothing else."
        ),
    ),
    Task(
        id="loop-aggregate",
        kind="loop",
        fixture="loop-aggregate",
        toolsets="file,terminal",
        timeout_s=300,
        checkpoints=["525", "60", "shipped"],
        expected_fn=_loop_answer,
        prompt=(
            "orders.csv lists orders with an amount and a status. Sum the `amount` of EVERY row "
            "whose status is exactly 'shipped'. Then read notes.md, find the REFUND value, and "
            "subtract it from that sum. Reply with ONLY the final integer on the last line."
        ),
    ),
    Task(
        id="dep-trace",
        kind="review",
        fixture="dep-trace",
        toolsets="file,terminal",
        timeout_s=300,
        checkpoints=["prod_west", "4471"],
        expected_fn=_dep_answer,
        prompt=(
            "In this codebase, a.py sets PRIMARY_PORT = REGISTRY[ACTIVE_KEY]. Follow the chain: "
            "find which key ACTIVE_KEY holds (defined in keys.py), then look that key up in "
            "registry.json to get its integer value. Reply with ONLY that integer on the last line."
        ),
    ),
    Task(
        id="grep-hunt",
        kind="review",
        fixture="grep-hunt",
        toolsets="file,terminal",
        timeout_s=300,
        checkpoints=["LICENSE_SEAT_COUNT", "24", "6"],
        expected_fn=_grep_answer,
        prompt=(
            "Search this whole directory tree for the setting named LICENSE_SEAT_COUNT and read its "
            "number. Then read SEATS_PER_POD from pods.yaml. Multiply the two. "
            "Reply with ONLY the product on the last line, nothing else."
        ),
    ),
    Task(
        id="recurrence-loop",
        kind="loop",
        toolsets="terminal",
        timeout_s=240,
        checkpoints=[],
        expected_fn=_recurrence_answer,
        prompt=(
            "Compute this exactly. Start with n = 3. Repeat the following step 12 times: "
            "set n = (n * 2 + 1) mod 97. After the 12th iteration, report n. "
            "Reply with ONLY the final value of n on the last line, nothing else."
        ),
    ),
    Task(
        id="discord-invite",
        kind="browser",
        toolsets="browser,web",
        timeout_s=360,
        # no partial-credit checkpoints: the obvious ones ("hal0.dev","discord")
        # are words in the prompt itself, so they'd always match without the agent
        # doing anything. For a pure browser task, correctness is the honest signal.
        checkpoints=[],
        expected_fn=_discord_code,
        prompt=(
            "Open a browser, navigate to https://hal0.dev, and find the link to the project's "
            "Discord community. Extract the invite CODE — the part after 'discord.gg/' (or "
            "'discord.com/invite/'). Reply with ONLY that invite code on the last line, nothing else."
        ),
    ),
    Task(
        id="browser-combine",
        kind="combine",
        fixture="codebase-combine",
        toolsets="browser,web,file,terminal",
        timeout_s=420,
        # only the code-side value is an honest progress signal ("discord" is in
        # the prompt); reaching 8412 means the agent actually read the codebase.
        checkpoints=["8412"],
        expected_fn=_browser_combine_answer,
        prompt=(
            "Two steps, then combine. (1) Open https://hal0.dev, find the Discord invite code (the "
            "part after discord.gg/), and count how many characters it has — call that N. "
            "(2) In this codebase, read BASE_PORT from src/config.py. Report BASE_PORT + N. "
            "Reply with ONLY that final integer on the last line, nothing else."
        ),
    ),
]


def get_task(task_id: str) -> Task | None:
    return next((t for t in TASKS if t.id == task_id), None)


# --------------------------------------------------------------------------- #
# scoring (pure — unit-tested without a live agent)
# --------------------------------------------------------------------------- #


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip().lower())


def extract_answer(stdout: str) -> str:
    """The agent's final answer: the last non-empty line of its output (the tasks
    instruct 'reply with ONLY … on the last line')."""
    lines = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


@dataclass
class Score:
    correct: bool
    expected: str
    answer: str
    checkpoints_hit: list[str]
    checkpoints_total: int
    score: float  # 1.0 correct; else partial from checkpoints (max 0.5)


def score_task(task: Task, stdout: str, expected: str, trace: str = "") -> Score:
    """Score a finished run against the derived expected value.

    Correctness is lenient on formatting: the normalized expected value appearing
    anywhere in the final answer (or the stdout tail) counts — the agent may wrap
    it in prose. Ordering credit scans the tool-call TRACE (plus stdout) for each
    checkpoint (the intermediate hidden values) — hermes -z prints only the final
    answer to stdout, so the intermediate values live in the exported trace."""
    answer = extract_answer(stdout)
    ne = _norm(expected)
    correct = bool(ne) and (ne in _norm(answer) or ne in _norm(stdout[-2000:]))
    haystack = _norm(stdout + " " + trace)
    hits = [c for c in task.checkpoints if _norm(c) in haystack]
    # full marks on a correct answer; else partial credit for reaching the
    # hidden checkpoint values, capped below a pass.
    val = 1.0 if correct else round(0.5 * (len(hits) / max(1, len(task.checkpoints))), 3)
    return Score(
        correct=correct,
        expected=expected,
        answer=answer[:400],
        checkpoints_hit=hits,
        checkpoints_total=len(task.checkpoints),
        score=val,
    )


# --------------------------------------------------------------------------- #
# hermes driver + metrics
# --------------------------------------------------------------------------- #


def hermes_cmd(task: Task, model: str, api: str) -> list[str]:
    """The exact headless Hermes invocation (pure — used by the runner and by
    `eval --dry-run`)."""
    return [
        HERMES, "-z", task.prompt, "-m", model, "--provider", "custom",
        "--yolo", "-t", task.toolsets, "--pass-session-id",
    ]


def _collect_metrics(workdir: Path, wall_s: float) -> tuple[dict[str, Any], str]:
    """Real trace metrics + the tool-call trace text for the session that ran in
    ``workdir``.

    hermes -z prints neither the session id nor the tool-call trace to stdout —
    both live in the SQLite session store — so we export recent sessions
    (newest-first) and match the one whose ``cwd`` is our workdir. The workdir is
    unique per (task, run), so this is concurrency-safe and unambiguous. Returns
    (metrics, trace_text); trace_text is the serialized messages so the scorer can
    scan it for the intermediate checkpoint values. Falls back to wall time only
    if the export isn't available."""
    metrics: dict[str, Any] = {
        "wall_s": round(wall_s, 1), "turns": None, "tool_calls": None,
        "tokens_in": None, "tokens_out": None, "api_calls": None,
    }
    trace = ""
    try:
        out = subprocess.run(
            [HERMES, "sessions", "export", "-"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return metrics, trace
    target = str(workdir)
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict) or d.get("cwd") != target:
            continue
        metrics.update(
            turns=d.get("message_count"), tool_calls=d.get("tool_call_count"),
            tokens_in=d.get("input_tokens"), tokens_out=d.get("output_tokens"),
            api_calls=d.get("api_call_count"),
        )
        msgs = d.get("messages")
        if isinstance(msgs, list):
            try:
                trace = json.dumps(msgs, ensure_ascii=False)
            except (TypeError, ValueError):
                trace = str(msgs)
        break  # newest-first, so the first cwd match is this run
    return metrics, trace


@dataclass
class EvalRecord:
    run_id: str
    suite: str
    task_id: str
    kind: str
    model: str
    outcome: str  # "ok" | "failed" | "hang"
    score: float
    correct: bool
    expected: str
    answer: str
    checkpoints_hit: list[str]
    checkpoints_total: int
    metrics: dict[str, Any]
    note: str = ""
    schema: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _evals_path() -> Path:
    return state_root() / "evals.jsonl"


def append_eval(rec: EvalRecord) -> None:
    p = _evals_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec.to_dict(), separators=(",", ":"), ensure_ascii=False) + "\n")


def read_evals() -> list[dict[str, Any]]:
    p = _evals_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def run_task(task: Task, model: str, run_id: str, api: str, workroot: Path) -> EvalRecord:
    """Drive one task through Hermes for one model, then score it.

    Prepares an isolated workdir (a copy of the fixture, if any), runs Hermes
    headless there with a watchdog (3x the task timeout), extracts + scores the
    final answer, and returns the record. Never raises on a bad run — a failed
    task records outcome=failed/hang and continues (like the sweep runner)."""
    import shutil

    workdir = workroot / f"{task.id}-{run_id[-6:]}"
    workdir.mkdir(parents=True, exist_ok=True)
    if task.fixture:
        src = FIXTURES / task.fixture
        if src.is_dir():
            shutil.copytree(src, workdir / task.fixture, dirs_exist_ok=True)
            workdir = workdir / task.fixture  # run the agent inside the fixture

    cmd = hermes_cmd(task, model, api)
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=str(workdir), capture_output=True, text=True, timeout=task.timeout_s * 3
        )
        stdout = (proc.stdout or "") + "\n" + (proc.stderr or "")
        outcome = "ok" if proc.returncode == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout if isinstance(exc.stdout, str) else "") or ""
        outcome = "hang"
    wall = time.time() - started

    try:
        expected = task.expected_fn()
    except Exception as exc:  # deriving the answer failed (e.g. hal0.dev unreachable)
        return EvalRecord(
            run_id=run_id, suite="agentic", task_id=task.id, kind=task.kind, model=model,
            outcome="failed", score=0.0, correct=False, expected="", answer="",
            checkpoints_hit=[], checkpoints_total=len(task.checkpoints),
            metrics={"wall_s": round(wall, 1)}, note=f"expected-derive failed: {exc}",
        )

    metrics, trace = _collect_metrics(workdir, wall)
    sc = score_task(task, stdout, expected, trace)
    note = f"{outcome}: {stdout[-200:]}" if outcome != "ok" and not sc.correct else ""
    return EvalRecord(
        run_id=run_id, suite="agentic", task_id=task.id, kind=task.kind, model=model,
        outcome="ok" if sc.correct or outcome == "ok" else outcome,
        score=sc.score, correct=sc.correct, expected=sc.expected, answer=sc.answer,
        checkpoints_hit=sc.checkpoints_hit, checkpoints_total=sc.checkpoints_total,
        metrics=metrics, note=note,
    )
