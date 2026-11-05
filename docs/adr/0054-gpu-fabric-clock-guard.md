# ADR-0054: GPU fabric-clock guard for NPU+GPU slots on Strix Halo

## Status

Accepted.

## Context

On AMD Strix Halo (gfx1151 iGPU + XDNA2 NPU), running NPU inference (hal0's
`flm` backend) while a GPU slot (Vulkan/ROCm/HIP) is also active can hang the
machine or corrupt NPU results. The root cause is the GPU's fabric clock
(fclk) changing DPM level mid-flight while the NPU is in use (upstream
ROCm/TheRock#8709); the fix belongs in BIOS/SMU firmware and is not in hal0's
control. The known mitigation is to pin the fabric clock at its top DPM
level — `power_dpm_force_performance_level=manual` plus the top
`pp_dpm_fclk` index, or `=high` — for as long as NPU and GPU work might run
together (halogen-flash-server's own deployment carries the same mitigation).

hal0 normally runs inside a privileged LXC on Proxmox. The sysfs files that
control the fabric clock
(`/sys/class/drm/cardN/device/power_dpm_force_performance_level`,
`pp_dpm_fclk`) are readable but **not** writable from inside that container —
only the Proxmox host can hold the clock. A guard that only runs in-container
can therefore never fix the hazard itself; it can only detect it and refuse
(or warn).

## Decision

- **Host side**: `scripts/proxmox-ve/hal0-fabric-clock` (POSIX `sh`,
  shellcheck-clean) holds, releases, and reports the fabric-clock state for
  the box's SoC GPU (preferring the device named by `/sys/class/kfd`
  topology as gfx1151, falling back to every amdgpu device with a
  fabric-clock control). `hal0-fabric-clock.service` is a `oneshot` systemd
  unit that runs it at boot and after suspend/hibernate/hybrid-sleep targets
  (the hold does not survive a resume). Both are installed on the **Proxmox
  host**, never inside the guest. Cost: about 1 W of idle power while held.
- **Guest side**: `hal0.slots.fabric_clock_guard` is a pure module (no I/O
  beyond an explicit `sys_root`, mirroring `hal0.slots.gtt_guard`) that reads
  the same sysfs files read-only, decides whether the clock is held, and
  composes the operator-facing message naming the exact host-side commands.
  `SlotManager.load()` calls `_guard_fabric_clock` (next to the existing
  `_guard_gtt_leak`) whenever the slot being loaded uses the NPU and another
  configured slot uses the GPU and is up/loading/unloading, or vice versa.
- **Policy** (`[slots].fabric_clock_guard`, env override
  `HAL0_FABRIC_CLOCK_GUARD`): `refuse` (default) raises
  `SlotFabricClockHazard` (409) and blocks the load; `warn` logs the hazard
  and stores the message on the slot's state for the dashboard, like the
  GTT-leak check, but still loads; `off` skips the check entirely. An
  unreadable clock state — the expected case inside hal0's own container,
  since the Proxmox host owns the write path but the read should still work
  through the LXC's sysfs view — always warns and never refuses: hal0 cannot
  tell a genuinely-unheld clock from a container boundary that hides the
  read, so it must not block on uncertainty.
- `hal0 doctor all` gains a `fabric_clock` row: PASS when no NPU+GPU slot
  pairing is configured or the clock is held, WARN with the host-side
  commands when both are configured and it is read as unheld, PASS
  ("unreadable") when the sysfs state cannot be read at all.

## Consequences

- An operator who runs NPU and GPU slots together on Strix Halo must install
  the host-side hold once; `hal0 doctor all` and a refused load both name the
  exact commands.
- A non-Strix-Halo box, or a box that never pairs an NPU slot with a GPU
  slot, is unaffected — the guard is a no-op whenever `slot_uses_npu` and
  `slot_uses_gpu` never both have an up/loading counterpart.
- The guard can only detect and refuse; it cannot write the sysfs files
  itself from inside the container by construction (privilege boundary), so
  the host-side script and unit remain the only way to actually fix the
  hazard.
