# Gateway connectivity — what hal0 can tell you

## Where the gateway's output goes

`hermes-gateway.service` sends stdout and stderr to journald (`StandardOutput=journal`,
`StandardError=journal` in the unit hal0 renders), so the platform connect and error lines are read with:

```bash
hal0 doctor logs --unit hermes-gateway -n 100
```

You are not in the `systemd-journal` group, so `journalctl` is denied to you; a person with root can
run `journalctl -u hermes-gateway -n 100 --no-pager`.

Hermes also keeps a `logs/` directory in its home, `/var/lib/hal0/.hermes/logs/`, which hal0
creates. hal0 does not document the file names Hermes writes there. Look at what is in it before
you rely on a name.

## A platform that does not connect

Work from the cause outward:

1. **Is the credential set?** You cannot read the vault or any `.env` file. See
   `hermes-gateway-platform-tokens.md` for the key-names check and for the `$HERMES_HOME/.env`
   override.
2. **Is the SDK in the live release?** "requirements not met" for the platform in the log means
   it is not. Ask the person to run `sudo hal0 agent reprovision hermes`, which builds a release
   when a configured platform's SDK is missing. `--repair` forces a build and restarts the agent
   and the gateway (your session drops, and it needs the network); say so before asking for it
   (see `../SKILL.md`).
3. **Is the network the problem?** The gateway needs outbound HTTPS to the platform. A firewall,
   proxy or DNS fault on the box or its network shows up as a network error in the journal. Test
   reachability of the platform's public host name from the box with the platform's own public
   endpoint; never put the token in the request you show the person.
4. **Is another gateway polling the same token?** hal0 refuses to enable its own gateway while a
   foreign `hermes-gateway` is live, because two pollers on one Telegram token make it answer
   HTTP 409 (`hal0 agent install hermes` prints how to stop the other one).

## What hal0 does not document

Hermes's own retry and fallback behaviour for a platform, its kanban dispatcher timing, and the file
descriptors of the gateway process are Hermes internals. If one of them matters, say what you saw and
ask the person to check Hermes's documentation for the pinned release.
