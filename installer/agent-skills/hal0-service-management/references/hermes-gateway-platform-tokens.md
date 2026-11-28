# hermes-gateway platform tokens — adding and rotating

The worked example referenced by `../SKILL.md`. It is the person's procedure: you never see,
ask for, print or store a token, and you do not touch the files below.

## Where a token lives

`hermes-gateway.service` runs as `hal0` and gets its platform credentials from one file, the
secrets vault `/var/lib/hal0/secrets/agents/hermes.env` (`root:root`, mode 0600, in a `0700`
root directory). hal0 wires it into the unit through the drop-in
`/etc/systemd/system/hermes-gateway.service.d/10-hal0-secrets.conf`
(`EnvironmentFile=-/var/lib/hal0/secrets/agents/hermes.env`). The variable names hal0 reads to
decide which platforms a box uses are `TELEGRAM_BOT_TOKEN` and `DISCORD_BOT_TOKEN` (one per platform; hal0
knows a longer list of platforms, each with its own variables).

hal0 has no command, dashboard page or admin tool that writes a Telegram or Discord token.
`provider_credential_write` is **not** it: it stores a model upstream provider's API key.
The vault is the file the person edits as root.

## Adding a token (the person)

1. As root, add the line `TELEGRAM_BOT_TOKEN=<token>` (or `DISCORD_BOT_TOKEN=<token>`) to the
   vault. hal0 keeps lines it did not write: its own writer merges by key, replaces a matching key
   and leaves comments and unrelated keys alone. Keep the file `root:root` 0600.
2. Make sure the live Hermes release carries that platform's SDK. A release is built with the SDK of
   every platform whose credentials are set, so a token added later needs a rebuild:
   `sudo hal0 agent reprovision hermes --repair` (the next `hal0 update` folds it in as well). Skip
   this step only if the platform connected before.
3. `sudo systemctl restart hermes-gateway`.
4. Check: `journalctl -u hermes-gateway -n 100 --no-pager` shows the platform's connect line and
   no "requirements not met" for it.

With no messaging platform configured, the gateway serves only the API server and cron; that is
normal, not a fault.

## Rotating a token (the person)

1. Create the new token in the platform's own bot console.
2. Replace the value of the same variable in the vault (as root), keeping the mode.
3. `sudo systemctl restart hermes-gateway`, then check the journal as above and send a test message.

Which users the bot answers is a Hermes setting that hal0 does not document: ask the person to set
it in Hermes's own configuration, and do not guess variable names.

## What you do

- Symptom "no messaging platforms enabled" or a platform that never connects: follow the diagnostic
  procedure in `../SKILL.md`, then tell the person which of the steps above is missing.
- Never ask for the token, never put one in chat, memory or a file, and never run a command that
  prints the vault. `hal0 agent status hermes` and the journal are safe; the vault is not.
