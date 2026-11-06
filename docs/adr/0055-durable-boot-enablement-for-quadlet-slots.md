# ADR-0055: Preserve Quadlet boot intent for autoload slots

## Status

Proposed — pending Alex's review of the operator-visible unload behavior.
Implementation is tracked by H0-275 and remains parked until this decision is
approved and merged.

## Context

`SlotConfig.autoload` is the operator's boot-start setting
(`src/hal0/config/schema.py`). The slot renderer uses
`hal0.slots.activation.autoload_enabled` to decide whether a generated Quadlet
contains `[Install] WantedBy=hal0.target`.

Quadlet does not support `systemctl enable` on its generated services; that
refusal is expected. Quadlet itself processes the `[Install]` section while
running its generator, creating the target's generated `.wants` link. A review
on 2026-11-05 observed that link on Podman 4.9.3 with systemd 255, consistent
with the [Podman Quadlet documentation](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html).

The actual lifecycle gap is that `ContainerProvider.unload_sync()` removes the
`.container` source after stopping the service. Once the source is absent,
Quadlet has nothing to generate at boot, regardless of the persisted
`autoload=true` setting. A target drop-in listing every configured autoload
slot would instead request units that may have no `.container` source and
therefore no generated service.

## Decision

Keep Quadlet `[Install] WantedBy=hal0.target` as the single boot-enablement
mechanism. The durable input is the `.container` source under
`/etc/containers/systemd/`; Quadlet regenerates the service and its target
`.wants` link at boot and on manager reload. Do not add a target drop-in, a
per-slot alias unit, or a manually maintained link into `/run`.

When an operator unloads a model-bound slot whose effective `autoload_enabled`
value is true, hal0 will stop the service and retain its `.container` source.
The container remains stopped for the current boot, while the durable Quadlet
definition preserves the configured boot-start behavior for the next boot.
The ordinary unload path continues removing the source when `autoload` is
false or the slot has no model binding.

Permanent removal of the boot intent must remove the `.container` source and
reload systemd: this includes disabling autoload, unbinding the model, and
deleting the slot. Delete and configuration-update paths must do this
explicitly even when they first stop the service through the unload path.

## Alternatives considered

1. **A target-level `slots.conf` listing configured autoload slots.** Rejected:
   slot configuration can outlive its generated unit source. After unload, the
   target would request a service that Quadlet did not generate. Keeping the
   `.container` definition with the slot lifecycle makes the source of the
   service and its boot intent the same artifact.
2. **Manually create links into `/run/systemd/generator/` or
   `hal0.target.wants/`.** Rejected: Quadlet owns the generated unit and its
   install links; the generator recreates those volatile outputs from the
   persistent `.container` source.
3. **Run `systemctl enable` on the generated service.** Rejected: systemd
   considers it generated/transient and refuses it; Quadlet applies `[Install]`
   while generating the unit.
4. **Remove the `.container` source on every unload.** Rejected: when the
   source is absent, the next Quadlet generation cannot create the autoload
   service or its `.wants` link, contradicting `autoload=true` for a bound
   model.

## Consequences and validation

- `autoload` remains owned by the slot config; no second roster or editable
  systemd configuration is introduced.
- `slot unload` on an autoload-enabled, model-bound slot means stop now and
  start again at boot. This operator-visible behavior must be confirmed by Alex
  and documented with the implementation.
- H0-275 must prove that Quadlet creates the `hal0.target.wants` link from the
  retained source; that unload retains the source only for the eligible state;
  and that disabling autoload, unbinding, and deletion remove the source and
  generated link. Tests must also show that manual unload leaves the service
  stopped during the current boot.
- No host-only systemd modification is required. Reboot validation remains
  with the Box Operator and is outside this decision issue.
