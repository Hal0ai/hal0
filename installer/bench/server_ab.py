#!/usr/bin/env python3
"""server_ab.py — server-level A/B measurement for hal0 slots (Tier B; Tier A
llama-bench cells run through hal0.bench.harness + the hal0-benchctl exec seam).

llama-bench cannot measure the server-only levers: MTP speculative decode
(``--spec-draft-*``), prompt-cache reuse (``--cache-reuse``), busy-wait poll,
or the embed/rerank endpoints. This script measures those against LIVE slots
through hal0-api + the slot's own llama-server port. It supersedes the ad-hoc
/root/bench_mtp.py with a versioned, restorable harness.

Bench Phase 3 (docs/superpowers/plans/2026-08-09-bench-phase3-oss-adapters.md):
the bench runner's ``chat`` cell kind no longer drives this script's ``ab``
mode — it now drives GuideLLM (``hal0.bench.adapters.guidellm``) against the
slot's HTTP endpoint directly, for real TTFT/ITL/TPOT distributions instead of
this script's hand-rolled sequential N-timed-calls loop. ``reuse``/``embed``/
``rerank`` (and ``batch``/``mtp``, not runner-wired) still measure server-only
levers GuideLLM has no vocabulary for, so this script is NOT retired — ``ab``
mode itself is kept working (manual/direct invocation), just no longer reached
by the automated planner/runner for the ``chat`` kind.

Runs as the unprivileged hal0/agent user — no sudo, no seam: it only talks to
hal0-api (same surface the dashboard uses). A tune lives on a PROFILE (ADR-0029),
never on the slot, so each arm gets a TEMPORARY custom profile cloned from the
slot's current profile (``POST /api/profiles``), the slot is pointed at it
(``PUT /api/slots/<slot>/config {"profile": ...}``) and restarted, and a
``finally`` block always points the slot back at its original profile and
deletes every temporary profile, on failure and on Ctrl-C too.

Modes
-----
  ab      Apply each --variant "label:<extra llama-server args>" as a temporary
          profile (the slot's profile flags first, the variant's after them, so
          last-wins dedup lets the variant override), restart, wait ready,
          run N timed /completion calls. Reports prefill+decode t/s medians and
          draft-acceptance when the server exposes it in timings. One variant =
          a plain labelled measurement; two or more = a side-by-side A/B.
          A label may carry profile settings: "label;ctx=8192;mtp=off:<flags>"
          (ctx = the profile's ctx_size, mtp = auto|off). --ctx-size / --mtp
          set the same for every arm.
  reuse   Built-in A/B of --cache-reuse 256 vs 0 on a shared-prefix trace:
          per variant, two /completion calls with the same long prefix and
          different suffixes; the second call's prompt timings show the reuse
          win (agentic TTFT proxy).
  embed   No config change: N timed POST /v1/embeddings calls with a long
          input; sanity-checks vector dims + reports latency.
  rerank  No config change: N timed POST /v1/rerank calls (query + docs);
          sanity-checks score spread + reports latency.
  batch   Continuous-batching sweep: for each --np value, apply a temporary
          profile whose flags end in `--parallel <np>`, restart, drive
          --concurrency simultaneous /completion streams, and report aggregate
          t/s, per-stream median, and p95 TTFT. The Tier C measurement behind
          the concurrency-batching plan. Restores the original profile.
  mtp     MTP draft-param sweep over --spec-nmax x --spec-pmin at --depth
          (2k/32k/128k) and a chosen sampler (--temp/--top-p/--top-k: greedy vs
          production). Relaunches per cell so the draft params take effect —
          the ROCmFPX runner at 7aa484a IGNORES the per-request speculative.*
          override (--per-request opts into it anyway, for a future build that
          honors it). Reports decode t/s + draft acceptance per cell (B-MTP).

Results: JSON to --out (default /var/lib/hal0/benchmarks/server-ab/<stamp>.json).

GPU rule: like llama-bench, numbers are only clean when nothing else is using
the GPU. This script talks to ONE slot; stop other GPU slots first (or accept
contention). It does not stop slots itself — the slot under test must be up.

Stdlib only (urllib) so it runs bare on the box without hal0's venv.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_API = "http://127.0.0.1:8080"
RESULT_DIR = Path("/var/lib/hal0/benchmarks/server-ab")

# ~2k-token-ish deterministic prompt (repeated technical text; no RNG so runs
# are comparable across boxes and days).
_PARA = (
    "The unified memory architecture of the Strix Halo platform allows the "
    "integrated GPU to address the full LPDDR5X pool, which changes the "
    "trade-offs for large language model inference: model weights are not "
    "copied across a PCIe boundary, prompt processing is compute bound while "
    "token generation is bandwidth bound, and the optimal batch and micro "
    "batch sizes differ from discrete GPU systems. "
)
LONG_PROMPT = _PARA * 40  # ≈ 2k tokens
REUSE_PREFIX = _PARA * 60  # ≈ 3k tokens shared prefix for the reuse trace

# _PARA is ~50 tokens; used to synthesize a prompt of an approximate token
# depth for the depth axis (runbook: tune at 2k AND 32k/128k, not just 2k).
_PARA_TOKENS = 50


def _build_prompt(depth_tokens: int) -> str:
    """A deterministic prompt of roughly *depth_tokens* tokens (repeated _PARA,
    at least one rep). Lets the same cell run at 2k / 32k / 128k fill so a
    'best' flag is reported per depth, not just at the shallow default."""
    reps = max(1, round(depth_tokens / _PARA_TOKENS))
    return _PARA * reps


# ── hal0-api auth (#2478) ─────────────────────────────────────────────────────
# On an auth-required box every hal0-api call needs the box service key. This
# script runs under the system python3 (no hal0 venv), so when hal0 is not
# importable it resolves the key exactly as hal0.service_identity does: env
# first, then api.env ($HAL0_HOME/etc/hal0 or /etc/hal0), the preferred tier
# first and the other tier as a fallback. Keys go ONLY to this box's hal0-api
# on loopback — never to a slot's own llama-server port, never to a remote
# --api host, and never across a redirect.
_KEY_ENV = {"admin": "HAL0_ADMIN_KEY", "client": "HAL0_CLIENT_KEY"}

# Stdlib copy of hal0.service_identity.is_loopback_url (strict allowlist).
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_loopback_url(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    if parts.username is not None:  # userinfo is never loopback (#2499)
        return False
    return host in _LOOPBACK_HOSTS


class _LoopbackOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Stdlib copy of the redirect handler in
    hal0.service_identity._no_proxy_opener: follow a redirect only to another
    loopback URL, refuse any other host with HTTPError."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if not _is_loopback_url(newurl):
            raise urllib.error.HTTPError(
                req.full_url, code, "redirect to a non-loopback host refused", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    """Stdlib copy of hal0.service_identity._no_proxy_opener."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _LoopbackOnlyRedirectHandler()
    )


def _open_url(req: urllib.request.Request | str, timeout: float) -> Any:
    """Stdlib copy of hal0.service_identity.open_url (#2505): a loopback
    target ignores the proxy environment, so the box key never reaches an
    HTTP_PROXY in cleartext, and follows redirects only to loopback; any
    other target honours the proxy as before."""
    url = req if isinstance(req, str) else req.full_url
    if _is_loopback_url(url):
        return _no_proxy_opener().open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def _api_env_path() -> Path:
    home = os.environ.get("HAL0_HOME", "").strip()
    return (Path(home) / "etc" / "hal0" if home else Path("/etc/hal0")) / "api.env"


def _keys_from_api_env() -> dict[str, str]:
    try:
        text = _api_env_path().read_text(encoding="utf-8")
    except Exception:
        return {}
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k in _KEY_ENV.values() and v:
            found[k] = v.strip().strip('"').strip("'")
    return found


def _api_auth_headers(prefer: str) -> dict[str, str]:
    """``{"Authorization": "Bearer <key>"}`` for the box identity, or ``{}``.

    ``prefer="client"`` for CLIENT routes (``GET /api/slots``);
    ``prefer="admin"`` only for the ADMIN routes this script drives
    (``PUT /api/slots/{slot}/config``, ``POST /api/slots/{slot}/restart``)."""
    try:
        from hal0.service_identity import service_auth_headers
    except ImportError:
        pass
    else:
        return service_auth_headers(prefer=prefer)
    order = ("admin", "client") if prefer == "admin" else ("client", "admin")
    for tier in order:
        key = os.environ.get(_KEY_ENV[tier], "").strip()
        if key:
            return {"Authorization": f"Bearer {key}"}
    file_keys = _keys_from_api_env()
    for tier in order:
        key = file_keys.get(_KEY_ENV[tier], "")
        if key:
            return {"Authorization": f"Bearer {key}"}
    return {}


def _http(
    method: str,
    url: str,
    body: dict | None = None,
    timeout: float = 600.0,
    auth: str | None = None,
) -> Any:
    """JSON request. ``auth`` ("client"/"admin") marks a hal0-api call and
    attaches the box key for that tier; ``None`` (a slot's own port) sends no
    key."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if auth is not None and _is_loopback_url(url):
        # Unredirected: urllib copies req.headers onto a redirect's new URL
        # whatever its origin, but never unredirected_hdrs.
        for name, value in _api_auth_headers(auth).items():
            req.add_unredirected_header(name, value)
    with _open_url(req, timeout=timeout) as resp:
        payload = resp.read()
    return json.loads(payload) if payload else {}


def _get_slot(api: str, name: str) -> dict:
    slots = _http("GET", f"{api}/api/slots", auth="client")
    items = slots if isinstance(slots, list) else slots.get("slots", slots.get("data", []))
    for s in items:
        if s.get("name") == name:
            return s
    sys.exit(f"slot {name!r} not found via {api}/api/slots")


def _wait_ready(port: int, timeout_s: float = 300.0) -> None:
    deadline = time.monotonic() + timeout_s
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        try:
            with _open_url(url, timeout=3) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    sys.exit(f"slot port {port} did not become healthy within {timeout_s:.0f}s")


def _sampler_body(args: argparse.Namespace) -> dict:
    """The sampler half of a /completion body, from the CLI axis. temp 0 =
    greedy (upper-bound MTP acceptance); a production sampler (e.g. temp 0.6
    top-p 0.95 top-k 20) is what agents actually see — the runbook runs both."""
    body: dict[str, Any] = {"temperature": float(getattr(args, "temp", 0.0) or 0.0)}
    top_p = getattr(args, "top_p", None)
    top_k = getattr(args, "top_k", None)
    if top_p is not None:
        body["top_p"] = float(top_p)
    if top_k is not None:
        body["top_k"] = int(top_k)
    return body


def _spec_override(n_max: int | None, p_min: float | None, n_min: int | None) -> dict:
    """Per-request `speculative.*` override (fork accepts n_max/n_min/p_min in
    the /completion JSON — MTP param sweeps run WITHOUT a server restart)."""
    spec: dict[str, Any] = {}
    if n_max is not None:
        spec["n_max"] = int(n_max)
    if n_min is not None:
        spec["n_min"] = int(n_min)
    if p_min is not None:
        spec["p_min"] = float(p_min)
    return {"speculative": spec} if spec else {}


def _completion(
    port: int,
    prompt: str,
    n_predict: int,
    cache_prompt: bool,
    *,
    sampler: dict | None = None,
    speculative: dict | None = None,
    ignore_eos: bool = True,
) -> dict:
    """One /completion call; returns llama-server's timings dict (+ our wall).

    ``sampler`` defaults to greedy (temp 0) for reproducibility; pass
    ``_sampler_body(args)`` for a production sampler. ``speculative`` carries a
    per-request MTP override (``_spec_override(...)['speculative']``).

    ``ignore_eos`` defaults True: a decode-throughput measurement needs the full
    ``n_predict`` tokens, but a strict chat template (e.g. the 35B's Froggeric)
    emits EOS after ~1 token on a raw /completion prompt, collapsing
    ``predicted_n`` to 1 and making the t/s meaningless. Off only for the reuse
    trace, where the natural stop is fine."""
    body: dict[str, Any] = {
        "prompt": prompt,
        "n_predict": n_predict,
        "cache_prompt": cache_prompt,
        "ignore_eos": ignore_eos,
    }
    body.update(sampler or {"temperature": 0})
    if speculative:
        body["speculative"] = speculative
    t0 = time.monotonic()
    out = _http("POST", f"http://127.0.0.1:{port}/completion", body)
    wall = time.monotonic() - t0
    t = dict(out.get("timings") or {})
    t["wall_s"] = round(wall, 3)
    return t


def _median(values: list[float]) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.median(vals), 2) if vals else None


def _summarize_runs(runs: list[dict]) -> dict:
    summary = {
        "prompt_per_second": _median([r.get("prompt_per_second") for r in runs]),
        "predicted_per_second": _median([r.get("predicted_per_second") for r in runs]),
        "wall_s": _median([r.get("wall_s") for r in runs]),
    }
    # Speculative-decode acceptance, when the server reports it (draft_n /
    # draft_n_accepted appear in timings only while a spec mode is active).
    drafted = sum(r.get("draft_n") or 0 for r in runs)
    accepted = sum(r.get("draft_n_accepted") or 0 for r in runs)
    if drafted:
        summary["draft_acceptance_pct"] = round(100.0 * accepted / drafted, 1)
    return summary


def _apply_profile(api: str, slot: str, profile: str) -> None:
    """Point the slot at ``profile`` (the config route's ``profile`` key only), restart.

    An empty name restores a slot that named no profile (it then runs its
    type's seed again)."""
    _http("PUT", f"{api}/api/slots/{slot}/config", {"profile": profile}, auth="admin")
    _http("POST", f"{api}/api/slots/{slot}/restart", {}, auth="admin")


def _resolved_command(api: str, slot: str) -> str | None:
    """The slot's effective launch command, from ``GET /api/slots/<slot>/resolved``.

    The runner image is dropped (argv[0]); ``None`` for a slot with no llama
    argv (non-llama engines) or when the route cannot be read."""
    try:
        detail = _http("GET", f"{api}/api/slots/{slot}/resolved", auth="client")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    argv = detail.get("argv") if isinstance(detail, dict) else None
    if not isinstance(argv, list) or len(argv) < 2:
        return None
    return " ".join(str(a) for a in argv[1:])


#: Marks a profile as one of this script's throwaway arms FOR ONE SLOT. The
#: intent carries the full slot name: profile names truncate it to 12 chars, so
#: two slots can share a name prefix, and the sweep must tell their arms apart.
def _arm_intent(slot_name: str) -> str:
    return f"temporary server_ab arm for slot {slot_name}"


def _arm_prefix(slot_name: str) -> str:
    return f"tune-{slot_name[:12]}-"


#: The seed a slot of this type runs when it names no profile.
_TYPE_SEED = {"embedding": "embedding", "reranking": "reranking"}


class _ProfileArms:
    """Temporary custom profiles for the arms of one measurement, always unwound.

    ``apply`` clones the slot's current profile with the arm's flags / ctx_size /
    mtp (POST /api/profiles), points the slot at the clone and restarts it.
    ``restore`` (call it from ``finally``) points the slot back at its original
    profile, restarts, then deletes every temporary profile. Each restore step
    is attempted even if an earlier one failed, and failures are reported
    loudly rather than swallowed."""

    def __init__(self, api: str, slot_name: str, slot: dict) -> None:
        self.api = api
        self.slot_name = slot_name
        self.original = str(slot.get("profile") or "")
        seed = self.original or _TYPE_SEED.get(str(slot.get("type") or ""), "chat")
        self.base: dict = _http("GET", f"{api}/api/profiles/{seed}", auth="client")
        self.created: list[str] = []
        self._applied = False

    def _next_name(self) -> str:
        # kebab-case, <=32 chars, leading alphanumeric (the profile name rule).
        return f"{_arm_prefix(self.slot_name)}{os.getpid() % 100000}-{len(self.created) + 1}"

    def apply(
        self,
        extra_flags: str = "",
        *,
        ctx_size: int | None = None,
        mtp: str | None = None,
    ) -> dict:
        """Create the arm's profile, point the slot at it and restart. Returns its body."""
        base_flags = str(self.base.get("flags") or "")
        body: dict[str, Any] = {
            "name": self._next_name(),
            # Appended after the originals: last-wins dedup lets the arm override.
            "flags": f"{base_flags} {extra_flags}".strip(),
            "vision": self.base.get("vision") or "auto",
            "speculation": mtp or self.base.get("speculation") or "auto",
            "cloned_from": self.base.get("name") or None,
            "intent": _arm_intent(self.slot_name),
        }
        window = ctx_size if ctx_size is not None else self.base.get("ctx_size")
        if window is not None:
            body["ctx_size"] = int(window)
        if self.base.get("enable_thinking") is not None:
            body["enable_thinking"] = self.base["enable_thinking"]
        if self.base.get("runner"):
            body["runner"] = self.base["runner"]
        _http("POST", f"{self.api}/api/profiles", body, auth="admin")
        self.created.append(body["name"])
        self._applied = True
        _apply_profile(self.api, self.slot_name, body["name"])
        return body

    def restore(self) -> None:
        problems: list[str] = []
        if self._applied:
            print(f"[tune] restoring slot profile = {self.original!r}", flush=True)
            try:
                _apply_profile(self.api, self.slot_name, self.original)
            except Exception as exc:  # keep going: the temp profiles must still go
                problems.append(f"could not restore profile {self.original!r}: {exc}")
        for name in self.created:
            try:
                _http("DELETE", f"{self.api}/api/profiles/{name}", auth="admin")
            except Exception as exc:
                problems.append(f"could not delete temporary profile {name!r}: {exc}")
        if problems:
            print(
                "[tune] WARNING — cleanup incomplete, fix by hand: " + "; ".join(problems),
                file=sys.stderr,
                flush=True,
            )


def sweep_stale_arm_profiles(api: str, slot_name: str) -> list[str]:
    """Delete leftover ``tune-<slot>-*`` arm profiles from a run that was killed hard.

    A ``finally`` cannot run on SIGKILL, so the next start for the slot cleans up.
    Only a profile named for this slot whose intent is EXACTLY this slot's arm marker
    (full slot name, not the truncated name prefix) is a candidate. One that a slot (or the API's own ``used_by``) still points at is
    NEVER deleted — it is warned about instead, since removing it would break that
    slot. Returns the deleted names; each deletion is logged."""
    deleted: list[str] = []
    try:
        profiles = _http("GET", f"{api}/api/profiles", auth="client")
        slots = _http("GET", f"{api}/api/slots", auth="client")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[tune] WARNING — stale-profile sweep skipped: {exc}", file=sys.stderr, flush=True)
        return deleted
    slot_rows = slots if isinstance(slots, list) else slots.get("slots", slots.get("data", []))
    pointed_at = {str(row.get("profile") or ""): row.get("name") for row in slot_rows}
    prefix = _arm_prefix(slot_name)
    intent = _arm_intent(slot_name)
    for item in profiles if isinstance(profiles, list) else []:
        name = str(item.get("name") or "")
        # Exact intent match: the name prefix alone is truncated and ambiguous.
        if not name.startswith(prefix) or str(item.get("intent") or "") != intent:
            continue
        users = sorted(
            {str(u) for u in item.get("used_by") or []}
            | ({str(pointed_at[name])} if name in pointed_at else set())
        )
        if users:
            print(
                f"[tune] WARNING — leftover arm profile {name!r} is still used by slot(s) "
                f"{', '.join(users)}; not deleting it. Point the slot back at its real profile "
                f"(`hal0 slot edit <slot> --profile <name>`), then delete {name!r}.",
                file=sys.stderr,
                flush=True,
            )
            continue
        try:
            _http("DELETE", f"{api}/api/profiles/{name}", auth="admin")
        except Exception as exc:
            print(
                f"[tune] WARNING — could not delete stale profile {name!r}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            continue
        print(f"[tune] removed stale temporary profile {name!r}", flush=True)
        deleted.append(name)
    return deleted


def _parse_variant(raw: str) -> tuple[str, str, int | None, str | None]:
    """``"label[;ctx=N][;mtp=auto|off]:<flags>"`` -> (label, flags, ctx_size, mtp)."""
    head, _, flags = raw.partition(":")
    label, *opts = [part.strip() for part in head.split(";")]
    ctx_size: int | None = None
    mtp: str | None = None
    for opt in opts:
        key, _, value = opt.partition("=")
        if key == "ctx" and value.strip().isdigit():
            ctx_size = int(value)
        elif key == "mtp" and value.strip() in ("auto", "off"):
            mtp = value.strip()
        else:
            sys.exit(f"bad variant option {opt!r} in {raw!r} (use ctx=N or mtp=auto|off)")
    flags = flags.strip()
    return (label or flags or "variant", flags, ctx_size, mtp)


def _arm_settings(args: argparse.Namespace) -> tuple[int | None, str | None]:
    """The run-wide --ctx-size / --mtp (a variant's own settings win over them)."""
    return getattr(args, "ctx_size", None), getattr(args, "mtp", None)


def mode_ab(args: argparse.Namespace) -> dict:
    slot = _get_slot(args.api, args.slot)
    port = int(slot["port"])
    arms = _ProfileArms(args.api, args.slot, slot)
    run_ctx, run_mtp = _arm_settings(args)
    variants = [_parse_variant(v) for v in args.variant]

    results: dict[str, Any] = {}
    try:
        for label, flags, v_ctx, v_mtp in variants:
            arm = arms.apply(
                flags,
                ctx_size=v_ctx if v_ctx is not None else run_ctx,
                mtp=v_mtp or run_mtp,
            )
            print(f"[ab] {label}: profile {arm['name']} flags = {arm['flags']!r}", flush=True)
            _wait_ready(port)
            runs = []
            for i in range(args.n):
                t = _completion(port, LONG_PROMPT, args.max_tokens, cache_prompt=False)
                print(f"  run {i + 1}/{args.n}: {t}", flush=True)
                runs.append(t)
            results[label] = {
                "extra_args": arm["flags"],
                "ctx_size": arm.get("ctx_size"),
                "mtp": arm["speculation"],
                "runs": runs,
                "median": _summarize_runs(runs),
            }
    finally:
        arms.restore()
    return results


def mode_reuse(args: argparse.Namespace) -> dict:
    slot = _get_slot(args.api, args.slot)
    port = int(slot["port"])
    arms = _ProfileArms(args.api, args.slot, slot)
    run_ctx, run_mtp = _arm_settings(args)

    results: dict[str, Any] = {}
    try:
        for label, flags in (
            ("cache-reuse-256", "--cache-reuse 256"),
            ("cache-reuse-0", "--cache-reuse 0"),
        ):
            arm = arms.apply(flags, ctx_size=run_ctx, mtp=run_mtp)
            merged = arm["flags"]
            print(f"[reuse] {label}: profile {arm['name']} flags = {merged!r}", flush=True)
            _wait_ready(port)
            # Call 1 warms the cache with prefix+A; call 2 (prefix+B) is the
            # measurement — with reuse the shared prefix is KV-shifted, without
            # it the whole prompt reprocesses.
            _completion(
                port,
                REUSE_PREFIX + "Summarize the first paragraph.",
                32,
                cache_prompt=True,
                ignore_eos=False,
            )
            second = _completion(
                port,
                REUSE_PREFIX + "List three key claims.",
                32,
                cache_prompt=True,
                ignore_eos=False,
            )
            print(f"  second-call timings: {second}", flush=True)
            results[label] = {"extra_args": merged, "second_call": second}
    finally:
        arms.restore()
    return results


def mode_embed(args: argparse.Namespace) -> dict:
    slot = _get_slot(args.api, args.slot)
    port = int(slot["port"])
    text = _PARA * 30  # long single input — exercises the -ub 8192 sizing rule
    lat, dims = [], None
    for i in range(args.n):
        t0 = time.monotonic()
        out = _http("POST", f"http://127.0.0.1:{port}/v1/embeddings", {"input": text})
        lat.append(round(time.monotonic() - t0, 3))
        vec = (out.get("data") or [{}])[0].get("embedding") or []
        dims = len(vec)
        if not dims or all(v == 0 for v in vec[:8]):
            sys.exit(f"embed sanity FAILED on run {i + 1}: dims={dims} (zero/empty vector)")
    return {"dims": dims, "latency_s": lat, "median_latency_s": _median(lat)}


def mode_rerank(args: argparse.Namespace) -> dict:
    slot = _get_slot(args.api, args.slot)
    port = int(slot["port"])
    docs = [
        _PARA,
        "Bananas are a good source of potassium and are yellow when ripe.",
        "Prompt processing on unified-memory APUs is compute bound.",
        "The weather in Reykjavik is frequently windy in winter.",
    ]
    lat, spread = [], None
    for i in range(args.n):
        t0 = time.monotonic()
        out = _http(
            "POST",
            f"http://127.0.0.1:{port}/v1/rerank",
            {
                "query": "How does unified memory change LLM inference trade-offs?",
                "documents": docs,
            },
        )
        lat.append(round(time.monotonic() - t0, 3))
        scores = [r.get("relevance_score", 0.0) for r in out.get("results", [])]
        if len(scores) != len(docs):
            sys.exit(f"rerank sanity FAILED on run {i + 1}: {len(scores)}/{len(docs)} scores")
        spread = round(max(scores) - min(scores), 4)
        if spread == 0:
            sys.exit(
                "rerank sanity FAILED: zero score spread — classifier head missing "
                "(bad GGUF conversion) or --embedding and --reranking combined on one instance"
            )
    return {"score_spread": spread, "latency_s": lat, "median_latency_s": _median(lat)}


def _one_stream(port: int, prompt: str, n_predict: int) -> dict:
    """A single concurrent /completion call (own prompt so slots don't collide
    on identical prefixes); returns per-stream timings + wall."""
    return _completion(port, prompt, n_predict, cache_prompt=True)


def mode_batch(args: argparse.Namespace) -> dict:
    """Continuous-batching sweep: for each --np value, apply a temporary profile
    whose flags end in `--parallel <np>`, restart, then drive C concurrent
    /completion streams and report AGGREGATE t/s, per-stream median, and TTFT
    spread — the numbers that decide whether a slot class should batch (plan
    Tier C). Streams share a long common prefix with distinct suffixes to
    exercise slot routing + prompt cache. Restores the original profile at
    the end.
    """
    import concurrent.futures as cf

    slot = _get_slot(args.api, args.slot)
    port = int(slot["port"])
    arms = _ProfileArms(args.api, args.slot, slot)
    run_ctx, run_mtp = _arm_settings(args)
    np_values = [int(x) for x in str(args.np).split(",") if x.strip()]

    results: dict[str, Any] = {}
    try:
        for np in np_values:
            conc = args.concurrency or np  # default: saturate the slots
            print(f"[batch] parallel={np}, concurrency={conc}", flush=True)
            arms.apply(f"--parallel {np}", ctx_size=run_ctx, mtp=run_mtp)
            _wait_ready(port)
            rounds: list[dict] = []
            for r in range(args.n):
                # Distinct suffix per stream so N sequences don't alias to one
                # cached slot; shared LONG_PROMPT prefix mimics agents sharing a
                # system prompt.
                prompts = [
                    f"{LONG_PROMPT} Request {i} round {r}: continue in one sentence."
                    for i in range(conc)
                ]
                t0 = time.monotonic()
                with cf.ThreadPoolExecutor(max_workers=conc) as ex:
                    streams = list(ex.map(lambda p: _one_stream(port, p, args.max_tokens), prompts))
                wall = time.monotonic() - t0
                gen = sum(s.get("predicted_n") or args.max_tokens for s in streams)
                rounds.append(
                    {
                        "wall_s": round(wall, 3),
                        "aggregate_tps": round(gen / wall, 2) if wall else None,
                        "per_stream_tps": [s.get("predicted_per_second") for s in streams],
                        "ttft_s": [s.get("prompt_ms", 0) / 1000.0 for s in streams],
                    }
                )
                print(
                    f"  round {r + 1}/{args.n}: aggregate {rounds[-1]['aggregate_tps']} t/s",
                    flush=True,
                )
            agg = [x["aggregate_tps"] for x in rounds]
            per = [v for x in rounds for v in x["per_stream_tps"]]
            ttft = [v for x in rounds for v in x["ttft_s"]]
            results[f"np{np}"] = {
                "parallel": np,
                "concurrency": conc,
                "rounds": rounds,
                "median": {
                    "aggregate_tps": _median(agg),
                    "per_stream_tps": _median(per),
                    "ttft_p95_s": round(sorted(ttft)[int(0.95 * (len(ttft) - 1))], 3)
                    if ttft
                    else None,
                },
            }
    finally:
        arms.restore()
    return results


def _csv_ints(raw: str) -> list[int]:
    return [int(x) for x in str(raw).split(",") if x.strip()]


def _csv_floats(raw: str) -> list[float]:
    return [float(x) for x in str(raw).split(",") if x.strip()]


def mode_mtp(args: argparse.Namespace) -> dict:
    """MTP draft-param sweep over the --spec-nmax x --spec-pmin grid at a given
    --depth and sampler, reporting decode t/s + draft acceptance per cell so the
    winner is picked on NET decode (greedy acceptance is a ceiling, not a ship
    metric). Draft-KV quant is NOT swept here (use --mode ab).

    Default is RELAUNCH-PER-VALUE: each cell applies a temporary profile whose
    flags carry `--spec-draft-n-max/-n-min/-p-min` and restarts (like mode_ab). The
    per-request `speculative.*` JSON override was tried first (it would let the
    whole grid run against one warm server) but the ROCmFPX runner at 7aa484a
    SILENTLY IGNORES it — every cell then reads the launch-time config and the
    sweep is meaningless. --per-request opts back into that path for a future
    runner build that honors it (with a loud caveat)."""
    slot = _get_slot(args.api, args.slot)
    port = int(slot["port"])
    arms = _ProfileArms(args.api, args.slot, slot)
    run_ctx, run_mtp = _arm_settings(args)
    prompt = _build_prompt(args.depth)
    sampler = _sampler_body(args)
    n_maxes = _csv_ints(args.spec_nmax)
    p_mins = _csv_floats(args.spec_pmin)
    n_min = int(args.spec_nmin)

    if args.per_request:
        print(
            "[mtp] WARNING: --per-request sends speculative.* in the /completion "
            "JSON; the ROCmFPX runner at 7aa484a IGNORES it (all cells read "
            "identical). Only use on a runner build known to honor it.",
            flush=True,
        )

    results: dict[str, Any] = {}
    try:
        for n_max in n_maxes:
            for p_min in p_mins:
                label = f"nmax{n_max}_pmin{p_min}"
                if args.per_request:
                    spec = _spec_override(n_max, p_min, n_min)["speculative"]
                    _wait_ready(port)
                else:
                    # Relaunch so the draft params actually take effect.
                    flags = (
                        f"--spec-draft-n-max {n_max} --spec-draft-n-min {n_min} "
                        f"--spec-draft-p-min {p_min}"
                    )
                    arms.apply(flags, ctx_size=run_ctx, mtp=run_mtp)
                    _wait_ready(port)
                    spec = None
                print(
                    f"[mtp] {label} (depth~{args.depth}, temp {sampler['temperature']}, "
                    f"{'per-request' if args.per_request else 'relaunch'})",
                    flush=True,
                )
                runs = []
                for i in range(args.n):
                    t = _completion(
                        port,
                        prompt,
                        args.max_tokens,
                        cache_prompt=False,
                        sampler=sampler,
                        speculative=spec,
                    )
                    print(f"  run {i + 1}/{args.n}: {t}", flush=True)
                    runs.append(t)
                results[label] = {
                    "n_max": n_max,
                    "p_min": p_min,
                    "n_min": n_min,
                    "runs": runs,
                    "median": _summarize_runs(runs),
                }
    finally:
        arms.restore()
    return results


def _provenance(args: argparse.Namespace, slot: dict | None) -> dict:
    """Reproducibility header (plan risk 6): the local-only runner image, its
    decode-tune profile, and the slot's resolved argv/env — without these the
    numbers can't be reproduced on a rebuilt box."""
    prov: dict[str, Any] = {}
    if args.runner_image:
        prov["runner_image"] = args.runner_image
    if args.decode_tune:
        prov["decode_tune"] = args.decode_tune
    if args.note:
        prov["note"] = args.note
    prov["depth_tokens"] = args.depth
    prov["sampler"] = _sampler_body(args)
    if slot is not None:
        prov["slot_resolved_args"] = _resolved_command(args.api, args.slot)
        prov["slot_profile"] = slot.get("profile") or None
        prov["slot_env"] = slot.get("env") or (slot.get("server") or {}).get("env")
    return prov


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--mode", required=True, choices=["ab", "reuse", "embed", "rerank", "batch", "mtp"]
    )
    ap.add_argument("--slot", required=True, help="slot name (must be loaded/running)")
    ap.add_argument("--api", default=DEFAULT_API, help=f"hal0-api base (default {DEFAULT_API})")
    ap.add_argument(
        "--variant",
        action="append",
        default=[],
        help='ab mode: "label[;ctx=N][;mtp=auto|off]:<extra llama-server args>" (repeatable)',
    )
    ap.add_argument(
        "--np",
        default="1,2,4,8",
        help="batch mode: comma list of `--parallel` values to sweep (default 1,2,4,8)",
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        default=0,
        help="batch mode: simultaneous streams per np (default 0 = match np)",
    )
    ap.add_argument(
        "--ctx-size",
        dest="ctx_size",
        type=int,
        default=None,
        help="every arm's profile ctx_size (default: the slot's current profile window)",
    )
    ap.add_argument(
        "--mtp",
        choices=["auto", "off"],
        default=None,
        help="every arm's profile mtp (default: the slot's current profile setting)",
    )
    ap.add_argument("--n", type=int, default=3, help="timed runs/rounds (default 3)")
    ap.add_argument("--max-tokens", type=int, default=256, help="decode length per run")
    ap.add_argument("--out", default=None, help="result JSON path (default under server-ab/)")
    # Depth axis — approximate prompt-fill in tokens (runbook: 2k/32k/128k).
    ap.add_argument(
        "--depth",
        type=int,
        default=2000,
        help="mtp mode: approximate prompt-fill in tokens (default 2000; runbook sweeps 2k/32k/128k)",
    )
    # Sampler axis — greedy (temp 0, acceptance ceiling) vs production sampler.
    ap.add_argument(
        "--temp", type=float, default=0.0, help="mtp mode: sampling temperature (0=greedy)"
    )
    ap.add_argument(
        "--top-p", dest="top_p", type=float, default=None, help="mtp mode: top-p (optional)"
    )
    ap.add_argument(
        "--top-k", dest="top_k", type=int, default=None, help="mtp mode: top-k (optional)"
    )
    # MTP draft-param grid. Relaunch-per-value by default (7aa484a ignores the
    # per-request override — see --per-request).
    ap.add_argument(
        "--spec-nmax", default="1,2,3,4", help="mtp mode: comma list of speculative n_max"
    )
    ap.add_argument(
        "--spec-pmin", default="0.0,0.25,0.5,0.75", help="mtp mode: comma list of speculative p_min"
    )
    ap.add_argument(
        "--spec-nmin", type=int, default=0, help="mtp mode: speculative n_min (default 0)"
    )
    ap.add_argument(
        "--per-request",
        action="store_true",
        help="mtp mode: send speculative.* per request instead of relaunching "
        "(IGNORED by the ROCmFPX runner at 7aa484a — only for a build that honors it)",
    )
    # Provenance (plan risk 6) — the local-only runner image is not reproducible
    # without capturing what built it.
    ap.add_argument("--runner-image", default=None, help="runner image ref for the results header")
    ap.add_argument(
        "--decode-tune", default=None, help="ROCMFP4_DECODE_TUNE profile for the header"
    )
    ap.add_argument("--note", default=None, help="free-text note for the results header")
    args = ap.parse_args()

    # One --variant is a plain labelled measurement (the bench runner's chat
    # cells pass exactly one, carrying the cell's config variant); two or more
    # is an A/B comparison. Zero would measure nothing.
    if args.mode == "ab" and not args.variant:
        ap.error("--mode ab needs at least one --variant entry")

    sweep_stale_arm_profiles(args.api, args.slot)

    fn = {
        "ab": mode_ab,
        "reuse": mode_reuse,
        "embed": mode_embed,
        "rerank": mode_rerank,
        "batch": mode_batch,
        "mtp": mode_mtp,
    }[args.mode]
    results = fn(args)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else RESULT_DIR / f"{stamp}-{args.mode}-{args.slot}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "timestamp": stamp,
        "mode": args.mode,
        "slot": args.slot,
        "n": args.n,
        "max_tokens": args.max_tokens,
        "provenance": _provenance(args, _get_slot(args.api, args.slot)),
        "results": results,
    }
    out_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out_path}")
    print(
        json.dumps(
            {k: v.get("median", v) if isinstance(v, dict) else v for k, v in results.items()},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
