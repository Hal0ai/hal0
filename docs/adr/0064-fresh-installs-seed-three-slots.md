# 0064. Fresh installs seed three slots, and the other jobs get theirs when they are switched on

## Status

Accepted 2026-11-28. Decided by the project's operator on #3431. Amends
[ADR-0023](0023-canonical-llm-roles-agent-utility.md) and
[ADR-0053](0053-memory-slot-and-chat-priority.md): this record finishes the
retirement of the `utility` name that ADR-0053 staged, and narrows what a fresh
install seeds. Publishing this record does not certify the installer steps
listed under Consequences as not done yet.

## Context

Every install used to copy ten slot files into `/etc/hal0/slots/`: `flm`,
`tts`, `rerank`, `memory`, `img`, `agent`, `brain`, `qwen3tts`, `coder` and
`embed`. Most of them sat empty. A fresh box showed ten tiles, seven of which
did nothing until someone chose a model, and the empty ones carried defaults
(a GPU device, a fixed port) that were wrong on some boxes, such as one with no
GPU. A request for embeddings, reranking or speech on such a box was routed to a
slot that had no model and ended in a bare `404 dispatch.no_route` that read
like a typo in the request.

ADR-0053 already stopped seeding `utility` and said the retirement would be
staged. The ten-slot list itself was never revisited.

## Decision

1. **A fresh install creates three slots: `agent`, `brain` and `memory`.** Under
   the installer's Customize path, each job the operator picks (images, the
   NPU, voice once the installer offers it) creates its own slot during the
   install. The NPU slot is also created on the recommended path when an NPU is
   found and passes its check. Nothing else is created.

2. **Two lists in `src/hal0/install/static_seeds.py` hold the two meanings.**
   - `SEED_TEMPLATES` is the ten seed files that exist under
     `installer/etc-hal0/slots/`. It is the vocabulary: which names are seeds
     (`is_seed`, the delete tombstone, the doctor's targets) and which slots a
     job may be created from.
   - `FRESH_INSTALL_SEEDS` is the three names a fresh install creates. It drives
     the boot-time seeding step, the installer's copy loop in
     `installer/install.sh`, and the scaffold in `hal0 setup --auto`. A test
     fails when the three places stop agreeing.

3. **An upgraded box keeps every slot it has, and a slot the operator deleted
   does not come back.** The boot step only copies a name that is absent and
   not tombstoned, and it now works from the smaller list. No code path deletes,
   renames or rewrites an existing slot file because of this decision.

4. **A job's slot is created from its seed when the job is switched on.**
   `ensure_seed_slot` copies the template for that name, gives a llama.cpp seed
   the device and context window derived from the box's hardware (so a box with
   no GPU gets `device = "cpu"`), and registers the slot with the manager. The
   same step runs when a job is switched on through the capability switch and
   when an image model is installed. If the seed's port is taken it takes a free
   one. A name the operator deleted on purpose is created again only by that
   explicit ask, which also clears the tombstone; boot never does it.

5. **A request for a job that is not set up gets a clear refusal.** Embeddings,
   reranking, speech, transcription and image generation answer
   `503 capability_not_enabled` when the box has no slot for the job. The body
   names the job and says how to turn it on, in the dashboard or with a command.
   Transcription on a box whose NPU serves it is unchanged. Chat is not a job in
   this sense: a chat request for a slot that does not exist still reaches
   `agent` through the routing chain.

6. **`hal0 setup --auto` scaffolds `agent` and `brain` only.** `memory` comes
   from its seed.

7. **`utility` is retired as a default.** Every built-in default (stacks,
   the helper lane, the Hermes auxiliary slot, the profile generator) says
   `memory`. What stays, for a box that has a `utility` slot or has addressed
   one:
   - `hal0/utility` resolves to the box's `utility` slot when it has one. On a
     box without one it takes the `hal0/memory` chain (`memory`, then `agent`).
   - An existing `utility` slot stays pinned against the idle reaper. An
     updater pass writes an explicit `pinned = true` to it once.
   - A stored `memory.graph.extraction_slot = "utility"` (written by config
     migration v4) keeps working.

   Each kept path carries a `HAL0-SUNSET: v2.0.0` marker naming what to delete,
   in the form `scripts/check_sunset.py` reads.

## Alternatives considered

- **Keep all ten slots on every install.** This is the old behaviour. It leaves
  most tiles empty, ships device and port defaults that do not fit every box,
  and makes a missing model look like a working slot. Rejected.
- **Delete the unused slots on upgrade.** It would make an upgraded box match a
  fresh one. It would also delete something the operator may have configured and
  later emptied, or may be about to use, and it breaks the rule that an update
  loses nothing. Rejected.

## Consequences

- A fresh box shows three slots. The others appear when their job is switched
  on, and are created with the box's own device and a free port.
- Seeding on demand adds one way for a slot file to appear: an explicit ask for
  a job. Tombstones still stop every automatic path.
- The 404 for an absent embeddings, rerank or speech slot becomes a 503 with the
  code `capability_not_enabled`. A client that keyed on `dispatch.no_route` for
  those jobs must key on the new code. The release-validation expectations were
  changed to match.
- Not done yet, and not to be described as working: the installer's own
  creation of the image and voice slots during a Customize install. The entry
  points exist (`python -m hal0.install.static_seeds --ensure <name>` and
  `python -m hal0.install.voice`) but `installer/install.sh` does not call them.
  The NPU slot at install is a later change; until then no install path creates
  `flm` on its own.
- The `utility` compatibility paths stay until v2.0.0 and are removed with the
  markers.
