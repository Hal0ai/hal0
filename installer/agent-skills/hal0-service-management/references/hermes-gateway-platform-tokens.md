# hermes-gateway platform tokens — adding and rotating

The worked example referenced by `../SKILL.md`. It is the person's procedure: you never see,
ask for, print or store a token, and you do not touch the files below.

## Where a token lives

`hermes-gateway.service` runs as `hal0` and gets platform credentials from two files:

1. The secrets vault `/var/lib/hal0/secrets/agents/hermes.env` (`root:root`, mode 0600, in a
   `0700` root directory). hal0 wires it into the unit through the drop-in
   `/etc/systemd/system/hermes-gateway.service.d/10-hal0-secrets.conf`
   (`EnvironmentFile=-/var/lib/hal0/secrets/agents/hermes.env`).
2. `$HERMES_HOME/.env`, normally `/var/lib/hal0/.hermes/.env`. The gateway loads it when it starts
   and it **overrides** the unit's environment, so a token left in `.env` beats a newer token in the
   vault. hal0's platform detection reads both files.

The variable names hal0 reads to decide which platforms a box uses are `TELEGRAM_BOT_TOKEN` and
`DISCORD_BOT_TOKEN` (one per platform; hal0 knows a longer list of platforms, each with its own
variables).

hal0 has no command, dashboard page or admin tool meant for an agent to set a Telegram or Discord
token; the person sets them.
`provider_credential_write` is **not** it: it stores a model upstream provider's API key.
The vault is the file the person edits as root.

To check whether a credential is set without reading any value, run
`sudo -n /usr/lib/hal0/bin/hal0-agentenv secret-keys hermes` (hal0's own sudo grant covers this
verb). It prints the NAMES of the vault keys that are set, one per line, never a value. If `sudo`
refuses, ask the person to run it. It does not cover `$HERMES_HOME/.env`.

## Adding a token (the person)

1. As root, add the line `TELEGRAM_BOT_TOKEN=<token>` (or `DISCORD_BOT_TOKEN=<token>`) to the
   vault. hal0 keeps lines it did not write: its own writer merges by key, replaces a matching key
   and leaves comments and unrelated keys alone. Keep the file `root:root` 0600.
2. Make sure the live Hermes release carries that platform's SDK. A release is built with the SDK of
   every platform whose credentials are set, so a token added later needs a rebuild. Run
   `sudo hal0 agent reprovision hermes` first: it builds a release when a configured platform's
   SDK is missing from the live one. The next `hal0 update` folds it in as well. Skip this step
   only if the platform connected before.
3. `sudo systemctl restart hermes-gateway`.
4. Check: `hal0 doctor logs --unit hermes-gateway -n 100` shows the platform's connect line and
   no "requirements not met" for it.

With no messaging platform configured, the gateway serves only the API server and cron; that is
normal, not a fault.

## Rotating a token (the person)

1. Create the new token in the platform's own bot console.
2. Replace the value of the same variable in the vault (as root), keeping the mode.
3. `sudo systemctl restart hermes-gateway`, then check the log as above and send a test message.
4. If the old token is still in use, ask the person whether `$HERMES_HOME/.env` carries a token for
   that platform. It overrides the vault; the person removes or updates it there.

Which users the bot answers is a Hermes setting that hal0 does not document: ask the person to set
it in Hermes's own configuration, and do not guess variable names.

## What you do

- Symptom "no messaging platforms enabled" or a platform that never connects: follow the diagnostic
  procedure in `../SKILL.md`, then tell the person which of the steps above is missing.
- If a rotation did not take, ask the person whether `$HERMES_HOME/.env` carries a token.
- Before you ask for `reprovision`, `--repair` or a restart, tell the person your own session will
  drop (see `../SKILL.md` for what `--repair` costs).
- Never ask for the token, never put one in chat, memory or a file, and never read or print the
  vault, any `.env` file or `/etc/hal0/api.env`. `hal0 agent status hermes` and
  `hal0 doctor logs --unit hermes-gateway` are safe.
