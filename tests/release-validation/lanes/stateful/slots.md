# Lane: slots (stateful, order 1)

Slot lifecycle end to end. You run first, so the box is in its post-install state — capture
what that state actually is before you change it.

## Checks

1. **Post-install baseline.** Record every slot, its assigned model, its state, and the unit
   status, before touching anything. On a fresh install this is a finding-rich moment: which
   slots ship seeded, which ship model-less, and does anything already look wrong?
2. **Assign + load.** Take the embed slot: `hal0 slot edit embed --model <embedding model>
   --hardware <backend>`, then `hal0 slot load embed`. Poll its health endpoint until ok (≤120 s
   on CPU). Watch the state transitions in `hal0 slot list` while it warms — do they progress
   sensibly (offline → warming → ready), and does the terminal state match the unit?
3. **A second slot.** Same flow for the rerank slot, so two are live at once.
4. **Restart / unload.** `hal0 slot restart <name>` — comes back healthy? `hal0 slot unload
   <name>` — clean stop, state offline, unit stopped, port released?
5. **Create / delete.** `hal0 slot create --help`, then create a throwaway slot on a free port
   (`hal0 ports`), assign a small chat model, load, verify health, unload, delete. Check for
   residue: unit files, `/var/lib/hal0/slots/<name>/`, claimed ports, registry entries. rc.4
   left an empty state directory behind.
6. **Swap on a healthy slot.** `hal0 slot swap <slot> --model <other model>`. Even if the CLI
   throws a `ReadTimeout`, wait and verify server-side: `systemctl cat hal0-slot@<slot> |
   grep -o -- '--model [^ ]*'` and the runner's own `/v1/models`. Then swap back and confirm
   healthy. Both the quadlet and the served model must change.
7. **Swap and lifecycle on an UNhealthy slot** — regression `crash-loop-lifecycle` (#1791).
   Deliberately induce a crash loop on a throwaway slot by assigning a model the runner cannot
   load. Then check: what state is reported while it crash-loops and after the systemd start
   limit is hit? Is the death surfaced anywhere a user would see (`hal0 status`, `doctor`, the
   dashboard)? Does swap write the NEW model? Clean the throwaway slot up afterwards.
8. **Profile flags reach argv** — regression `profile-flags-argv` (#1787), the rc.4 GA-blocker.
   For each loaded slot, compare its profile's flags against the resolved runner argv. Include
   at least one model that has never been stamped with `defaults.profile` (register a fresh
   gguf via `hal0 model add` and assign it) — the unstamped path is the one that broke.
9. `hal0 slot metrics` and `hal0 slot capacity` reflect what is actually loaded, with correct
   units — rc.4 booked CPU memory under "VRAM MB" with "RAM MB 0.0".

## Leave behind

Brain slot loaded and healthy on the CPU-viable chat model; embed slot loaded (the memory lane
uses it); rerank unloaded; every throwaway slot deleted. State all of this in
`box_state_on_exit`.
