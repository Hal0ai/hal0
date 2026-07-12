# Honcho packaging

Self-hosted [Honcho](https://github.com/plastic-labs/honcho) v3 (memory
engine: FastAPI + pgvector + redis), run as a docker-compose stack managed by
`hal0-honcho.service`.

## Provenance

`docker-compose.yml` is vendored from upstream's
`docker-compose.yml.example` at the pin below, adapted for hal0 (see the
comment header in that file for the exact deltas — port 5432 conflicts with
Hindsight's embedded pg0, bind mounts replace named volumes, etc). It is not
a symlink or generated file — edit it directly and keep the deltas in sync
if upstream's example changes shape.

Pinned ref: `main@73453f89 (pin: v3.0.9 lacks STRUCTURED_OUTPUT_MODE needed for local llama backends)` (`HONCHO_REF` in `installer/install.sh`).

## Upgrading

1. Bump `HONCHO_REF` in `installer/install.sh` (the default) or set
   `HONCHO_REF=vX.Y.Z` for a one-off install.
2. Re-run the installer, or by hand:
   ```
   rm -rf /var/lib/hal0/honcho/src
   git clone --branch <new-ref> --depth 1 \
     https://github.com/plastic-labs/honcho /var/lib/hal0/honcho/src
   ```
3. Diff the new `docker-compose.yml.example` against the previous upstream
   tag; port any new services/env vars into `installer/honcho/docker-compose.yml`.
4. `podman compose --project-name hal0-honcho -f /var/lib/hal0/honcho/docker-compose.yml build`
5. `systemctl restart hal0-honcho`

## License note

Honcho is AGPL-3.0. This packaging runs it self-hosted for hal0's own
in-box memory pipeline only — we don't redistribute the built image or
offer Honcho as a network service to third parties, so AGPL's
network-copyleft clause doesn't trigger. Don't publish the built
`hal0-honcho` image to a registry without revisiting this.
