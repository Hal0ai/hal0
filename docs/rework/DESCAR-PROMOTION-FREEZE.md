# ⛔ DESCAR FROZEN — descar → main promotion in progress (R5 / v1.0.0 staging)

**To any other agent/session writing to `rework/descar`: HOLD ALL PUSHES until this file is deleted.**

The orchestrator session is promoting `rework/descar` → `main` (PR #1318). This requires a
stable tip with a clean, uncancelled CI run. Concurrent pushes tangle history and cancel CI
(observed: `d1b5af3c` CI cancelled by a concurrent merge `ea938bdf`).

If you are the session on branch `claude/hal0-rework-handoff-o4fcws`: please stop pushing to
`rework/descar` and coordinate. Your work is not lost — descar will be promoted as-is, then
unfrozen (this file removed) so you can continue.

Frozen at tip: `ea938bdf` · Reason: descar→main promotion · Unfreeze: delete this file after merge.
