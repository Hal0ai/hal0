"""TUI shell driving metrics_core.FleetMetrics by keystroke.

PROTOTYPE — wipe me once the model is validated. The logic lives in
metrics_core.py; this file just exposes it as keys + a frame renderer
so a human can poke at the model and see what shakes out.

Run:    python3 tui.py           (or `make proto-ttft` from repo root)
Quit:   x
"""

from __future__ import annotations

import random
import sys
import termios
import time
import tty

from metrics_core import FleetMetrics

SLOTS = ["primary", "embed", "embed-rerank", "stt", "tts"]
LLAMA = {"primary", "embed", "embed-rerank"}  # only these have a KV cache
START_KEYS = {"1": 0, "2": 1, "3": 2, "4": 3, "5": 4}
FIRST_KEYS = {"q": 0, "w": 1, "e": 2, "r": 3, "t": 4}

fleet = FleetMetrics(window_s=60.0)
for name in LLAMA:
    fleet.set_kv_cache(name, 0.0)
req_n = [0]

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
GREEN = "\x1b[32m"
YELL = "\x1b[33m"
RED = "\x1b[31m"
RST = "\x1b[0m"


def fmt_ms(v: float | None) -> str:
    if v is None:
        return f"{DIM}—{RST}"
    ms = v * 1000.0
    colour = GREEN if ms < 200 else YELL if ms < 800 else RED
    return f"{colour}{ms:6.0f} ms{RST}"


def fmt_pct(v: float | None) -> str:
    if v is None:
        return f"{DIM}—{RST}"
    pct = v * 100.0
    colour = GREEN if pct < 50 else YELL if pct < 85 else RED
    return f"{colour}{pct:5.1f} %{RST}"


def render() -> None:
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.write(f"{BOLD}TTFT + KV-cache PROTOTYPE{RST}  ")
    sys.stdout.write(f"{DIM}t={time.monotonic():7.1f}s · window={fleet.window_s:.0f}s{RST}\n\n")
    sys.stdout.write(f"  {BOLD}Per slot{RST}\n")
    sys.stdout.write(
        f"  {DIM}{'slot':<14}{'ttft now':>14}{'ttft avg':>14}{'kv-cache':>14}{'inflt':>7}{'samples':>9}{RST}\n"
    )
    for name in SLOTS:
        s = fleet.slot(name)
        kv = fleet.kv_cache.get(name)
        sys.stdout.write(
            f"  {name:<14}{fmt_ms(s.current_ttft()):>23}{fmt_ms(s.avg_ttft()):>23}"
            f"{fmt_pct(kv):>23}{len(s.inflight):>7}{s.sample_count():>9}\n"
        )
    sys.stdout.write(
        f"\n  {BOLD}Fleet{RST}   ttft avg {fmt_ms(fleet.avg_ttft())}   "
        f"kv-cache avg {fmt_pct(fleet.avg_kv_cache())}\n\n"
    )
    sys.stdout.write(f"  {DIM}Keys:{RST}\n")
    sys.stdout.write(f"  {BOLD}1-5{RST} start req on slot N        ")
    sys.stdout.write(f"{BOLD}q w e r t{RST} first-chunk arrives on slot N\n")
    sys.stdout.write(f"  {BOLD}k{RST} bump kv-cache (llama only)   ")
    sys.stdout.write(f"{BOLD}d{RST} drop kv-cache\n")
    sys.stdout.write(f"  {BOLD}c{RST} cancel oldest inflight       ")
    sys.stdout.write(f"{BOLD}s{RST} simulate concurrent burst on primary\n")
    sys.stdout.write(f"  {BOLD}a{RST} age out all samples (jump clock)   ")
    sys.stdout.write(f"{BOLD}R{RST} reset state    {BOLD}x{RST} quit\n")
    sys.stdout.flush()


def start_req(idx: int) -> None:
    req_n[0] += 1
    fleet.slot(SLOTS[idx]).request_started(f"r{req_n[0]}")


def first_chunk(idx: int) -> None:
    slot = fleet.slot(SLOTS[idx])
    if not slot.inflight:
        return
    rid = next(iter(slot.inflight))
    slot.first_chunk(rid)


def cancel_oldest() -> None:
    for s in fleet.slots.values():
        if s.inflight:
            rid = next(iter(s.inflight))
            s.request_cancelled(rid)
            return


def bump_kv() -> None:
    for name in LLAMA:
        cur = fleet.kv_cache.get(name, 0.0)
        fleet.set_kv_cache(name, cur + 0.07 + random.random() * 0.03)


def drop_kv() -> None:
    for name in LLAMA:
        cur = fleet.kv_cache.get(name, 0.0)
        fleet.set_kv_cache(name, cur - 0.10)


def burst() -> None:
    now = time.monotonic()
    for delay in (0.05, 0.08, 0.25, 0.60):
        req_n[0] += 1
        rid = f"r{req_n[0]}"
        fleet.slot("primary").request_started(rid, now=now)
        fleet.slot("primary").first_chunk(rid, now=now + delay)


def age_out() -> None:
    """Rewind every sample's timestamp past the window so they drop
    out — lets us watch the avgs go back to '—' without sitting at
    the terminal for a minute."""
    horizon = time.monotonic() - fleet.window_s - 1.0
    for s in fleet.slots.values():
        s.ttft_samples = type(s.ttft_samples)(
            ((min(ts, horizon), t) for ts, t in s.ttft_samples),
            maxlen=s.ttft_samples.maxlen,
        )


def reset_all() -> None:
    fleet.slots.clear()
    fleet.kv_cache.clear()
    for name in LLAMA:
        fleet.set_kv_cache(name, 0.0)
    req_n[0] = 0


def main() -> None:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        render()
        while True:
            ch = sys.stdin.read(1)
            if ch == "x":
                break
            if ch in START_KEYS:
                start_req(START_KEYS[ch])
            elif ch in FIRST_KEYS:
                first_chunk(FIRST_KEYS[ch])
            elif ch == "k":
                bump_kv()
            elif ch == "d":
                drop_kv()
            elif ch == "c":
                cancel_oldest()
            elif ch == "s":
                burst()
            elif ch == "a":
                age_out()
            elif ch == "R":
                reset_all()
            else:
                continue
            render()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
