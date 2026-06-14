# hermes-gateway platform tokens — wiring & rotation

Session: 2026-06-02. After 788 restart cycles, hermes-gateway came up with zero platform adapters even though `TELEGRAM_BOT_TOKEN` and `DISCORD_BOT_TOKEN` were present in `/var/lib/hal0/secrets/agents/hermes.env`. Root cause: the user-level systemd unit lacked `EnvironmentFile=`. Fix: add the line, daemon-reload, restart.

This file is the worked example referenced by `../SKILL.md`.

## Symptoms

```
gateway.log:
  Gateway running with 0 platform(s)
  Channel directory built: 0 target(s)
  No messaging platforms enabled

# /proc/<pid>/environ shows PATH, VIRTUAL_ENV, HERMES_HOME — nothing else
# /var/lib/hal0/secrets/agents/hermes.env has TELEGRAM_BOT_TOKEN, DISCORD_BOT_TOKEN, *_ALLOWED_USERS, *_HOME_CHANNEL
```

## Pre-fix unit

```ini
[Unit]
Description=Hermes Gateway
After=network-online.target

[Service]
Type=simple
Environment="PATH=/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="VIRTUAL_ENV=/root/.local/share/hermes/venv"
Environment="HERMES_HOME=/root/.config/hermes"
ExecStart=/root/.local/bin/hermes-gateway
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

Notice: no `EnvironmentFile=`. The process has only the three `Environment=` keys, never the secrets.

## Fix (one line)

```ini
[Service]
EnvironmentFile=/var/lib/hal0/secrets/agents/hermes.env   # ← added
Environment="PATH=..."
# ...rest unchanged
```

```bash
cp /root/.config/systemd/user/hermes-gateway.service{,.bak-$(date +%F)}
# edit the unit
systemctl --user daemon-reload
systemctl --user restart hermes-gateway
```

## Post-fix verification

```bash
journalctl --user -u hermes-gateway -n 50 --no-pager | grep -iE 'platform|connect'
# expected:
#   gateway.run: Gateway running with 2 platform(s)
#   gateway.platforms.telegram: [Telegram] Connected to Telegram (polling mode)
#   gateway.platforms.discord: [Discord] Connected as hal0#0276

# ground-truth check
scripts/check-service-env.sh hermes-gateway TELEGRAM_BOT_TOKEN DISCORD_BOT_TOKEN
# expected: both vars OK
```

## Token rotation (separate workflow)

Telegram:

1. Revoke the old token via @BotFather on Telegram (`/revoke` → choose bot → confirm).
2. @BotFather issues a new token in the chat. Copy it.
3. Validate it: `curl -s "https://api.telegram.org/bot<NEW_TOKEN>/getMe"` should return a JSON with `"ok": true`.
4. Write to the secrets file **through the provisioner** (do not hand-edit if there's a blessed path):
   ```bash
   hal0-admin:provider_credential_write \
     --provider telegram \
     --token "<NEW_TOKEN>" \
     --allowed-users "<comma,sep,ids>" \
     --home-channel "<chat_id>"
   ```
   This regenerates `/var/lib/hal0/secrets/agents/hermes.env` with correct perms.
5. Restart the gateway: `systemctl --user restart hermes-gateway`.
6. Re-run the verification above. Round-trip a DM to the new bot.

Discord:

1. Discord bot tokens can only be **regenerated** at the developer portal (https://discord.com/developers/applications → bot → "Reset Token"). The old one is destroyed.
2. Same provisioner path; same restart.

## Why this happened (theory)

The hal0 provisioner (`hermes_provision.py::_merge_env_file`) writes the secrets file. It does **not** wire the gateway's systemd unit. The wiring step was probably done in an earlier bootstrap of the gateway but predates the current hal0 provisioner (the running gateway is a manual user-level service, not a `hal0-agent@<name>.service` instance — see the `hal0 agent provision hermes --repair` note in the session reply). The fix is to either:
- Add `EnvironmentFile=` to the unit (what we did — minimal, surgical)
- Move the gateway to a managed `hal0-agent@<service>` unit and re-provision

The second is cleaner long-term but is a larger change. Tracked as a follow-up, not a blocker.

## Files in this case

| Path | Role | Edited? |
|------|------|---------|
| `/root/.config/systemd/user/hermes-gateway.service` | user unit | yes — added `EnvironmentFile=` |
| `/root/.config/systemd/user/hermes-gateway.service.bak-pre-telegram-fix` | backup of pre-fix unit | created (timestamped backup) |
| `/var/lib/hal0/secrets/agents/hermes.env` | secrets env file (provisioner-owned) | no — token was already valid + present |
| `/var/lib/hal0/state/hermes-gateway/logs/gateway.log` | gateway log | auto-written |
| `/var/lib/hal0/state/hermes-gateway/channel_directory.json` | channel cache | auto-written |

## Follow-up: access-control hardening (same session, ~30 min later)

User asked to lock the bot down: add their Discord user ID to `DISCORD_ALLOWED_USERS` and set `GATEWAY_ALLOW_ALL_USERS=false`. The home channel (`DISCORD_HOME_CHANNEL=1507565140365672650` = `#hal0-agent`) was already correct.

Final values written to `/var/lib/hal0/secrets/agents/hermes.env`:

```
DISCORD_ALLOWED_USERS=257021675260870657,1507564468203294900   # existing user + new one
GATEWAY_ALLOW_ALL_USERS=false
```

`GATEWAY_ALLOW_ALL_USERS=false` is a permanent change — it activates the per-platform allowlists globally, including the Telegram one (`TELEGRAM_ALLOWED_USERS=8382890357`, which is the user's Telegram ID). The Telegram bot stays usable for the user and only the user.

**Net effect:** Telegram and Discord both platforms connected, both with per-platform allowlists enforced. The default-on state of `GATEWAY_ALLOW_ALL_USERS=true` is the wrong starting point for any internet-reachable bot — if any other hal0 service on this host ships with it `true`, that's a future finding.

## Follow-up: the `patch` tool race on systemd units

First attempt to add `EnvironmentFile=` to the unit used `patch`. It reported `success: true` with a clean diff. The mtime on the file bumped. But after a `daemon-reload` + `restart`, the gateway log said "No user allowlists configured" / "No messaging platforms enabled" — and `cat` of the unit file showed the line was *not* present. The file was byte-identical to the pre-patch backup (same md5). Something between the patch tool's read and its write had reverted the file content, but preserved the mtime bump.

Workaround that worked: `write_file` to overwrite the whole unit file with the new content, then `daemon-reload` + `restart`. After that, `systemctl --user show hermes-gateway.service -p EnvironmentFiles` returned `EnvironmentFiles=/var/lib/hal0/secrets/agents/hermes.env (ignore_errors=no)` and `/proc/<pid>/environ` had all 7 expected vars. The `write_file` tool itself emitted a warning: "was modified since you last read it on disk (external edit or unrecorded writer). Re-read the file before writing." — which is the fingerprint of the race.

Rule going forward: for any `/root/.config/systemd/user/*.service` file on this host, prefer `write_file` over `patch`. The cost is having to re-emit the full file content; the benefit is atomicity.

## Reusable tools

- `scripts/check-service-env.sh hermes-gateway TELEGRAM_BOT_TOKEN DISCORD_BOT_TOKEN DISCORD_ALLOWED_USERS GATEWAY_ALLOW_ALL_USERS` — runs in <1s, reports which vars are present/missing. Use before AND after any env-wiring change.
- For /proc/<pid>/environ reads, prefer Python over shell pipelines if the result is suspiciously empty.
