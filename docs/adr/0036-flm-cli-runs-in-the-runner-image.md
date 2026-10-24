# ADR-0036: `flm pull` and `flm list` run inside the flm runner image

## Status

**ACCEPTED (2026-10-24).** Closes #2993. Applies ADR-0006 (every shipped
image is a registry runner) to the FastFlowLM CLI: the runner image is the one
FastFlowLM version that decides what hal0 serves and what it downloads.

## Context

The NPU slot serves from the `flm` runner image (`FLMProvider.container_spec`,
`src/hal0/providers/flm.py`). Model pulls and the NPU catalogue probe did not:
they ran the host `/usr/bin/flm` from the FastFlowLM `.deb`.

- Only `installer/install.sh` installs that `.deb`. Nothing under
  `src/hal0/updater/` touches it, so a box that only runs `hal0 update` keeps
  the host version it was installed with.
- When a release moves the runner image to a new FastFlowLM version, the old
  host binary still picks the weights. FastFlowLM 1.0.2 and 1.0.3 changed the
  Qwen3.5 and Qwen3.6-MoE weights, models added after the host version are
  missing from the host catalogue, and 1.0.7 renamed `hy-mt2:1.8b` to
  `hy-mt2-flash:1.8b`. Each of these puts the wrong weights, or no weights,
  in the store the new container serves.
- `src/hal0/slots/flm_catalog.py` tried `podman exec hal0-slot-flm` first.
  hal0-api runs as the unprivileged `hal0` user, slot containers live in
  root's podman store, and slots are named by instance token, so that exec
  never reached the slot and the catalogue always fell back to the host
  binary.

The issue offered two fixes: run the CLI inside the runner image, or make
the privileged update step upgrade the host `.deb`. The second keeps two
copies of FastFlowLM that must agree, and adds a root package install to
every update.

## Decision

1. **`flm pull` runs in a one-shot container of the slot's image.**
   `flm_pull_command` (`src/hal0/providers/flm.py`) resolves the FLM
   anchor slot's image (`FLMProvider.image_ref`, which honours `image_pin`,
   `[slots].default_images` and the runner pin table) and store
   (`FLMProvider.models_store`), the same two values the slot
   launch uses (`resolve_target` in `src/hal0/providers/flm_cli.py`). The
   store is mounted at `.config/flm/models` under the container's HOME, the
   path the slot mounts it at under its own (`_CONTAINER_FLM_MODELS`). A pull
   always uses a one-shot container, even while the slot serves: killing a
   `podman exec` client does not stop the process inside, so an exec'd pull
   could not be cancelled. There is no host fallback for a pull
   (`pull_invocation` in `src/hal0/providers/flm_cli.py`). A store that does
   not exist yet is created first, with the shape the slot launch gives it
   (`require_flm_store`), as the host pull path did.
2. **`flm list` tries the running slot, then a one-shot, then the host.**
   `flm_list_output` runs `podman exec` into the FLM slot
   container when it is up, else a one-shot container of its image. The host
   binary answers only when neither route can, and that case is logged as
   `flm.cli_host_fallback`. The catalogue still renders on a box without the
   seam; its tags may then lag the runner.
3. **`flm validate` stays on the host** (`flm_validate`).
   Setup runs it to decide whether to set up the NPU lane, before any runner
   image is pulled. It checks host facts (driver, firmware, memlock) and
   writes nothing to the store.
4. **A new, separately revocable seam, `hal0-flmctl`, carries the root
   side.** `installer/wrappers/hal0-flmctl` plus
   `packaging/sudoers/hal0-flmctl`. `hal0-podman-ro` stays read-only and
   `hal0-podman-rw` stays image-store-only. Every podman subcommand and flag
   is a literal in the wrapper. Callers pass only a slot instance token, an
   image reference (OCI grammar, the same validator as the other podman
   seams), the store directory and a model tag, and each is validated root
   side. The one-shot container:
   - pins the entrypoint to `tini -- /opt/fastflowlm/bin/flm`, so the image a
     caller names cannot choose which program runs;
   - runs as the hal0 account's own uid, primary gid and supplementary
     groups, read from the account databases by the wrapper and never from
     the caller, which is what the old host `flm pull` ran as. It has
     `--cap-drop=all` and `no-new-privileges` and gets no devices. Its HOME
     is a fresh tmpfs, because the image's own HOME is mode 0700 for uid
     1000, and the store is mounted at `.config/flm/models` under it;
   - takes a store that is not a system directory or a broad parent such as
     `/home`, `/var` or `/mnt` (checked after symlinks are resolved), and
     that is handed to the account: owned by its uid or group-owned by one
     of its groups;
   - is named `hal0-flm-pull-<tag>` for a pull, so the `stop-pull <tag>` verb
     can remove it;
   - uses `--pull=never`. A missing image is exit 70, which the pull job
     reports as "download the flm runner image first". Runner images still
     arrive only through the manifest-verified runner pull.

   **The boundary, stated honestly.** The store is a caller operand. Its
   location is runtime configuration (`[models].flm_store` in `hal0.toml`,
   which the hal0 account itself writes), so there is no root-owned record
   to pin it to. The boundary therefore rests on identity: whatever directory
   is mounted, the process writing to it has exactly the hal0 account's
   rights, so a different store buys no write the account did not already
   have. A mount does add reach: a bind mount skips the search permission
   of the source's parents, so a directory the account could not walk to by
   path becomes reachable. The denylist and the ownership hand-off narrow
   that to directories already given to the account. The check and the
   mount are not atomic: an account that owns a parent of the store can
   swap it for a symlink between them, which changes the mounted directory
   but not the identity, so it reduces to the same reach-only gap. This
   seam is not a sandbox against a compromised hal0 account. It is a hard
   boundary against running anything but flm, against more than the
   account's own write rights, and against extra podman flags.
5. **Update-only boxes get the seam.** `hal0-flmctl` is in
   `hal0.system.seam_check.SEAMS`, the inventory
   `refresh_privileged_wrappers` (`src/hal0/updater/updater.py`)
   reinstalls from on every activate. `install.sh`, `uninstall.sh`, the
   preflight seam probe and `hal0 doctor` carry it too. Its side-effect-free
   probe verb is `check-tag`.
6. **Cancelling a pull removes its container.** The pull job sends SIGTERM,
   which podman's signal proxy forwards to flm. If the process is still
   running after 10 s, the job runs `stop-pull <tag>` (`podman rm --force`
   on the named container) before it kills the process, because podman does
   not proxy SIGKILL and an orphaned container would keep writing.

## Consequences

- The runner image's FastFlowLM version decides which weights are pulled and
  which tags exist. The host `.deb` version matters only for `flm validate`,
  so #2993 no longer needs the updater to upgrade the `.deb`.
- An FLM pull needs the `flm` runner image in root's store. Before, a box
  with the `.deb` and no image could pull weights it could not serve. Now
  the pull fails with a clear error until the image is downloaded.
- The one-shot needs FastFlowLM's `list` and `pull` to work without the NPU
  device and as a uid the image does not know. That was checked on Strix
  Halo with the current runner image: as the hal0 account (uid 996, not
  1000), with the pinned entrypoint, `--cap-drop=all` and no devices, the
  wrapper's `run-list` answered and `run-pull embed-gemma:300m` downloaded
  and verified the model, with the files owned by the hal0 account. A
  SIGTERM to the wrapper stopped and removed the pull container.
- Weights are owned by the hal0 account, as host pulls left them. The
  serving slot (uid 1000) reads them through the usual 0644/0755 modes.
- The host store symlink (`ensure_host_flm_store_link`) is no longer on the
  pull path. It still serves the host `flm list` fallback and any `flm` an
  operator runs by hand, and `hal0 doctor perms` still audits it.
- A pull started while the slot serves runs a second container against the
  same store. FastFlowLM's pull writes per-model directories, and the slot
  only reads the model it has loaded.
