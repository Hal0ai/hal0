# Linear -> Superset workspace handoff

Wires Linear's **Work on issue -> Custom script** to Superset: clicking it on
a Hal0 Linear issue creates a git-worktree workspace on the issue's branch
with Claude already running the prompt. Background:
<https://docs.superset.sh/use-with-linear>.

These files are templates. They belong in `~/.linear/` on **your Mac**, not
in this repo checkout — run these steps locally, not from a Cowork sandbox.

## Prerequisites

- Superset CLI installed and signed in: `superset auth login`.
- Superset desktop app installed.
- hal0 registered as a local Superset project (see
  `docs/agents/superset-integration.md` "One-time setup") — grab its id with
  `superset projects list`.

## Install

```sh
mkdir -p ~/.linear
cp scripts/superset/open-in-superset.sh ~/.linear/open-in-superset.sh
chmod +x ~/.linear/open-in-superset.sh

cp scripts/superset/coding-tools.json.example ~/.linear/coding-tools.json
```

Edit both copies:

- `~/.linear/open-in-superset.sh`: replace `PROJECT="<hal0-superset-project-id>"`
  with the real id.
- `~/.linear/coding-tools.json`: replace `/Users/you/.linear/open-in-superset.sh`
  with the absolute path on your machine (`~` is not expanded here).

If Linear already wrote a starter `~/.linear/coding-tools.json` when you
enabled the feature, merge the `openIssue` block in rather than overwriting
the file.

## Enable in Linear

1. **Settings -> Code & reviews -> Configure coding tools** -> enable
   **Custom script**.
2. On any Hal0 issue: **Work on issue -> Custom script**. First run, Linear
   asks you to choose a working directory — pick your hal0 checkout (the
   script itself doesn't read it, but Linear requires one).

## Customizing

- **No agent, just the branch**: drop `--agent`/`--prompt` from the script.
- **Different agent**: swap `claude` for `codex`, `opencode`, etc.
- Full reference: <https://docs.superset.sh/use-with-linear>.
