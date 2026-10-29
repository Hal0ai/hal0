---
name: hal0-bridge
category: homelab-ops
description: How to answer a task delegated by another agent through hal0's agent bridge, with a fenced result block first, then what was done and what awaits approval, questions only through the clarify tool, and no trailing offers. Use when a run's input comes from a remote coding agent (Claude Code, Codex, a CI job) rather than from the operator in chat.
---

# Answering a delegated task

Another agent started this run through hal0's agent bridge. It reads your final
message with a program or a model and usually cannot ask you again, so make the
message complete, short and in a fixed shape.

## The reply

Open the final message with one fenced `result` block, then the prose below it.

```result
status: ok | partial | failed | needs-approval
summary: one line saying what happened
artifacts: paths or URLs you produced, or none
needs: what is blocking you or what must happen next, or none
```

- `ok` means done as asked. `partial` means some of it is done; say what is left
  under `needs`. `failed` means nothing usable came out; `summary` says why.
  `needs-approval` means a gated action is waiting for the operator.
- `artifacts` are absolute paths or URLs the caller can open: a file you wrote, a
  pull request, a report. Write `none` when there are none.
- Keep each field to one line and no markdown inside the block.

After the block:

1. **What was done.** A few sentences or short bullets of facts, with what you ran
   or checked and what it showed. Do not retell the task.
2. **What awaits approval.** Each action the operator must allow in the hal0 inbox
   (a command Hermes gated, a skill proposed with `agent_skill_put`), what it
   unblocks, and that you cannot approve it yourself. Leave this out when nothing
   is waiting.

## Questions

If you need something from the caller, ask it through the clarify tool. The run
waits and the caller answers it as a question. Never end a message with a question
or "let me know" in prose: nobody is there to read it and the run ends as finished.

## Do not

- Offer follow-ups ("I can also...", "want me to..."). Say what is left in `needs`.
- Open with greetings or apologies, or close with a summary of the summary.
- Paste secrets, keys or tokens. Name where they live instead.
- Report `ok` for work you did not verify. An honest `partial` beats a guessed `ok`.
