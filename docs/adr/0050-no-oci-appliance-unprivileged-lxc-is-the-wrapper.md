# ADR-0050: hal0 ships no OCI appliance; unprivileged LXC is the wrapper

## Status

**ACCEPTED (decided 2026-11-01, recorded 2026-11-02).** Records a decision the
operator took while the Incus install guide (#3255) was being written. Nothing
about an install changes with this record.

## Context

A bare-metal install puts hal0 deep into the host: systemd units, a `hal0`
service user with sudo grants, a rootful podman store and a self-updater that
owns `/usr/lib/hal0`. The question was whether wrapping hal0 in a Docker or
podman image would contain that root exposure.

Two facts shape the answer.

hal0 is itself a container orchestrator. Podman's systemd generator turns each
`hal0-slot@<id>.container` into a unit (`installer/wrappers/hal0-systemctl`),
and those slots run under rootful podman and open the GPU and NPU device nodes.
An OCI image of the control plane would need systemd and nested podman inside
the image, or the host's container socket. That socket is root on the host.

A wrapper does not change what happens inside it. `SECURITY.md` records that the
`hal0` account is root-equivalent by design, and that an agent made to run a
command can reach every credential the box holds. The axis that matters there is
which user the agent runs as. ADR-0002 and ADR-0024 carry that analysis.

## Decision

1. hal0 publishes no OCI image or compose file of its control plane. Slots stay
   containers that hal0 orchestrates, and podman stays the runtime inside the
   box.
2. An unprivileged LXC system container is the one wrapper: `pct` on Proxmox
   (`docs/getting-started/proxmox.mdx`) and `incus` on plain Linux
   (`docs/getting-started/incus.mdx`). A bare-metal install stays supported for
   a dedicated box.
3. The wrapper protects the host, not the credentials inside the box. Isolation
   inside the box is the agent user split, tracked in #3134 for v1.9.0, together
   with the agent key tier and the removal from the `hal0` group that ADR-0024
   and `SECURITY.md` say it needs.
4. Rootless slots are not pursued until that split ships. ADR-0002 records that
   the split removes the agent's route to authoring rootful slot containers, so
   the question is reopened then, against what is left. The account a slot's
   process runs as inside its container is a separate item (#3126).
5. The Proxmox quick path stays privileged by default for now. Its unprivileged
   path is validated on Strix Halo with `OS_VERSION=24.04`, and the script
   defaults to 26.04. #3327 flips the default after that run.

## Consequences

- There is no second distribution format to build, sign and update.
- An operator who wants hal0 off the host installs LXC tooling. There is no
  `docker run` path.
- Inside an unprivileged container hal0 cannot repair `/dev/kfd`'s gid. A wrong
  mapping is fixed by the host's `devN:` entry
  (`docs/getting-started/proxmox.mdx`).
- Nested guests carry their own installer cases: the AppArmor socket denial
  (#3250) and the subuid range (#3301).
- Parts of the Incus guide have not been exercised, and #3328 lists them.
- A wrapper gives the operator a clean host and an easy uninstall. It does not
  close the credential chain in `SECURITY.md`, and no guide may present it as
  doing so.
- This record does not touch the probes that fall back to a `docker` binary, such
  as `installer/lib/preflight.sh` and `src/hal0/slots/capacity.py`.

## Alternatives considered

- **OCI image with nested podman.** It needs systemd, rootful podman and the
  device nodes inside an application image. A system container provides those as
  its normal shape, with an ordinary unprivileged user as its root on the host.
- **OCI image using the host's container socket.** The socket is root on the
  host, which gives up the containment the wrapper was for.
- **Bare metal only.** Rejected because an operator sharing a workstation with
  other work wants a host hal0 has not changed, and a delete as the uninstall.

## Relationship to other records

- **ADR-0002.** Its Option B, the user split, is the isolation this record points
  to. A wrapper is not an alternative to it.
- **ADR-0024.** Item 4 is the same split. Item 1, the agent key tier, has to ship
  with it, because an agent holding the box admin key keeps the API whatever user
  it runs as.

## References

- `SECURITY.md`, "Bundled agent trust boundary".
- `docs/getting-started/incus.mdx`, `docs/getting-started/proxmox.mdx`,
  `scripts/proxmox-ve/hal0.sh`.
- #3134, #3126, #3250, #3255, #3301, #3327, #3328.
