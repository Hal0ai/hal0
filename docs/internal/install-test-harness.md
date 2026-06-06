# Install test harness (#407)

The fresh-install smoke harness clones an ephemeral Proxmox LXC, runs a full
**install → smoke → uninstall → destroy** cycle on a byte-identical clean box,
asserts zero residue, and emits one JSON result line.

## Why it exists

PR CI (`pytest` + UI build) **never executes `install.sh`** — that needs root,
systemd, and a real machine. So changes to the installer, updater, or uninstall
path are invisible to CI: a fully-green PR can still break every fresh install.
This harness is the regression net for that gap. It is the prerequisite for the
FHS-layout work (#495) and install-mode reconciliation (#406), where "tests
pass" does not mean "a real install works."

It already paid for itself on the first run: it caught that the Ubuntu base
image ships `python3` without `python3-venv`, which `preflight_venv` (#497)
correctly refuses — exactly the class of breakage CI can't see.

## Pieces

| Path | What |
|---|---|
| `scripts/fresh-test-ct.sh` | the harness (clone → install → smoke → uninstall → assert → destroy → JSON) |
| `packaging/proxmox/hal0-test-template/` | how to (re)build the CT-200 golden template + the provisioning artifacts |
| `make harness-install` | runs the harness, appends the JSON line to `tests/harness/reports/install-smoke.jsonl` |

## Usage

```bash
# install the CURRENT working tree's installer (verifies your code / a PR):
make harness-install ARGS="--from-tree $(pwd)"

# install the LIVE published release (curl hal0.dev/install.sh | bash):
make harness-install

# keep the clone for inspection on failure; mount the shared model store:
bash scripts/fresh-test-ct.sh --from-tree $(pwd) --keep --with-models
```

`--from-tree <dir>` rsyncs the tree to the clone and runs its
`installer/install.sh` (with `HAL0_INSTALL_SKIP_VERIFY=1`, since it isn't a
signed bootstrap handoff). Default mode installs `https://hal0.dev/install.sh`,
which serves the last **published** tarball — note that may predate unmerged
fixes on `main`, so use `--from-tree` to verify current code.

### Output

One JSON line per run:

```json
{"row":"install-smoke","clone_id":990,"install_ok":true,"smoke_ok":true,
 "uninstall_ok":true,"residue":"clean","ip":"10.0.1.193","elapsed_s":210}
```

`residue` is `clean` only when the uninstall left **no** hal0 paths
(`/opt/hal0`, `/opt/lemonade`, `/usr/lib/hal0/current`, `/etc/hal0`,
`/var/lib/hal0`, the bin symlinks), **no** systemd units, and **no** `hal0`
group. Any residue flips `uninstall_ok` to false.

## Env knobs

| Var | Default | Meaning |
|---|---|---|
| `HAL0_TEST_TEMPLATE` | `200` | golden template VMID |
| `HAL0_PVE` | `pve` | SSH alias for the Proxmox host |
| `HAL0_TEST_KEY` | `~/.ssh/thin-mint` | key for pve + the clone's `halo` user |
| `HAL0_INSTALL_URL` | `https://hal0.dev/install.sh` | live-mode install URL |

## Gotchas (learned building this)

- **kfd stop-hang.** A privileged LXC holding `/dev/kfd` (after the install
  warms ROCm) hangs on `lxc-stop --kill`. The harness cleanup SIGKILLs the
  `lxc-start -F -n <vmid>` monitor first, then `pct destroy --force --purge`.
  Without this, clones leak as un-destroyable running CTs.
- **Tailscale DNS.** The pve host's `/etc/resolv.conf` points at the Tailscale
  resolver `100.100.100.100`, which does not work inside a CT. The template
  pins `--nameserver 10.0.1.1 10.0.1.200`.
- **No bind mount in templates.** `pct template` refuses a CT with an
  `/mnt/ai-models` bind mount; it's added per-clone via `--with-models`.
- **apparmor + docker.** docker can't load `docker-default` in an
  apparmor-unconfined LXC; the template purges apparmor (see
  `dreamserver_ct108_eval` memory).

## Refreshing the golden template

See `packaging/proxmox/hal0-test-template/README.md`. In short: rebuild from
scratch (~2 min) or clone → `apt upgrade` → re-`provision.sh` → re-`pct template`.
Bump the base image with `pveam download` when a newer Ubuntu point release lands.

## Future

- `--mode=editable` to exercise `install.sh --dev` once #406 lands.
- Wire `make harness-install ARGS="--from-tree ."` into a nightly (not per-PR;
  it needs the Proxmox host + several minutes) once #495 makes the install
  layout stable.
