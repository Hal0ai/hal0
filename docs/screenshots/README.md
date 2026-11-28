# Dashboard screenshots

The source set of hal0 dashboard screenshots for the hal0.dev website and the
documentation. Every image here is produced by a script in this repository and
can be re-shot after a UI change; nothing was captured from a real box.

These files are not served from here. The website and the published docs load
images from the `Hal0ai/hal0-web` repository (`public/screenshots/`), so an
image reaches hal0.dev only when it is copied there. The **Replaces** column
names the hal0-web file an image is meant to replace; where a name already
appears in these docs or in `README.md`, the file here uses the same name so it
can be dropped in without changing any link. Issue #3629 lists five docs pages
that lost their screenshot; the rows marked "(#3629)" are the replacements.

## How the set is made

- **Dashboard build:** the Vite dev server for `ui/`, at the commit this
  folder was last regenerated from (v1.8.2, including the phone and tablet
  layout from #3956, #3964, #3965 and #3967).
- **Data:** the dashboard's own mock layer (`ui/src/api/mock.ts`, the same one
  the end-to-end suite uses) with the e2e fixtures from
  `ui/tests/e2e/fixtures/`, plus a showcase overlay in
  `ui/scripts/screenshots/showcase/` that turns it into one busy, healthy box:
  a Ryzen AI Max+ 395 with 128 GB, three chat slots serving, the NPU in use,
  memory on two banks, Hermes and Pi installed. The Settings pages read
  `/api/settings` bodies generated from hal0's own config schema at its
  defaults (`ui/scripts/screenshots/gen_settings_data.py`).
- **Browser:** Chromium from `@playwright/test`, device scale factor 2, dark
  theme (the dashboard has one theme), animations frozen, no scrollbars.
- **Viewports:** desktop 1440×900 (the Overview hero at 1680×1050, so its
  tables and cards fit without clipping), tablet 834×1112, phone 390×844.
  Close-ups are cropped to what one element draws, at desktop width.
- **Format:** lossless PNG, each under 1.5 MB.

To re-shoot the whole set, from `ui/`:

```sh
npx playwright test -c playwright.screenshots.config.ts
```

`SHOT_ONLY=<regex>` re-shoots only the images whose names match, and
`SHOT_OUT=<dir>` writes somewhere else. The capture specs are in
`ui/scripts/screenshots/` (`pages.shot.ts`, `responsive.shot.ts`,
`closeups.shot.ts`); they are not part of the end-to-end suite and CI does not
run them. A shot fails, rather than writing a larger file, if its PNG is over
1.5 MB. After a change to hal0's config schema, run
`python ui/scripts/screenshots/gen_settings_data.py` from the repository root
before re-shooting the Settings pages.

## What the numbers in the images mean

The images show a mock box, so read the figures in them this way. The
**Figures** column in the table says which kind each image carries.

- **historical** — published measurements, used as-is:
  - the two GPU chat models, their decode and prefill speed, speculative
    accept rate and memory, are from the hal0.dev/benchmarks leaderboard sweep
    of **2026-06-19** on the reference box (Ryzen AI Max+ 395, 128 GB,
    `rocm` lane), taken on an early hal0 build with the `rocmfp4` llama.cpp
    fork. The leaderboard notes that the Standard runner has replaced that
    fork since, so current numbers differ. Figures used:
    `qwen3.6-35b-a3b-crown-halo-mtp-dynamic` 84.4 tok/s decode, 873 tok/s
    prefill, 90.9 % accept, 22.6 GB; `qwen3-coder-reap-25b-a3b-q5km` 54.7 /
    1,368 tok/s, 17.7 GB; on the Benchmarks page also `gemma-4-26b-a4b`
    40.9 / 1,336 tok/s, 17.1 GB and `qwen3.5-0.8b` 169.8 / 6,248 tok/s,
    0.6 GB;
  - the NPU chat model, `gemma3:1b` at about 38.6 tok/s in about 2 GB, and
    Kokoro's 0.18 real-time factor, are from the "Benchmarks" section of
    `README.md`, a separate measurement from the leaderboard sweep.
- **illustrative** — invented for the picture, with no published source: GPU,
  CPU and NPU utilisation, clock, temperature and power readings; the shape of
  the throughput and activity charts (each lane is capped at its historical
  decode figure); the 24-hour request and token counts; embed and rerank
  latency; and the memory of the small slots.
- **none** — no performance figures.

Every figure that appears on more than one image comes from one per-slot
table (`SLOT_FIGURES` in `ui/scripts/screenshots/showcase/core.ts`): the
combined throughput (177.7 tok/s: 84.4 + 54.7 + 38.6), the serving count, the
GPU pool's used and free memory (116 GB pool), each slot's memory and the
model sizes in the catalogue are all derived from it, so the images agree with
each other. A slot's memory is the size the catalogue lists for its model; for
the two GPU chat models that is the leaderboard's per-run memory, and the
three NPU models add up to the README's ~2 GB. Two figures differ by design:
the Slots page's Inference engine panel leaves NPU slots out of its serving
count ("2 serving"), and it rounds free memory to whole gigabytes
(`ui/src/dash/inference-pane.jsx`).

The dashboard itself clips some text, and the images show it as it is
rather than hiding it: the support-slot cards on Slots ▸ Inference cut the
model name ("nomic", "bge-r", "kokor"), the one-line dock on a phone cuts the
services count ("5 / 5 re"), and the dock ticker cuts long source names.
`phone-slots.png` and `closeup-slot-cards.png` show this most and are marked
for docs use only.
On the Services page the ComfyUI card's warning glyph renders as an empty box
in `services-page.png`. That may come from the capture browser's fonts rather
than the dashboard; it has not been checked in a desktop browser.

Images marked illustrative, or historical + illustrative, should not be used
as a hero image or in a benchmark section without fresh numbers from a real
box. The model names are ones the reference box has run (the leaderboard) or
that hal0 ships (FLM, Kokoro, Whisper, the seed catalogue). Host names, URLs
and keys are placeholders (`hal0.local`, `hal0.example`, `127.0.0.1`).

## The images

| File | Route | Viewport | Pixels | What it shows | Suggested use | Replaces (hal0-web `public/screenshots/`) | Figures |
|---|---|---|---|---|---|---|---|
| `agents-overview.png` | `#agent` | 1440×900 | 2880×1800 | Agents, Overview: Hermes, Pi and the coming-soon cards | feature section | agents-overview.png | illustrative |
| `benchmarks.png` | `#benchmarks` | 1440×900 | 2880×1800 | Benchmarks, Roster: four models with decode, prefill and memory | feature section (benchmarks) | — | historical |
| `closeup-add-provider-dialog.png` | `#models/providers + Add provider` | 1440×900, element | 1550×762 | Close-up: the Add provider dialog | close-up, docs step | — | none |
| `closeup-agent-cards.png` | `#agent` | 1440×900, element | 2136×1695 | Close-up: agent cards | close-up | — | illustrative |
| `closeup-benchmarks.png` | `#benchmarks` | 1440×900, element | 2384×852 | Close-up: the benchmark roster | close-up (benchmarks) | — | historical |
| `closeup-dock-expanded.png` | `#slots + dock` | 1440×900, element | 2880×694 | Close-up: the expanded dock, events tab | close-up | — | none |
| `closeup-image-gen-pane.png` | `#slots/image` | 1440×900, element | 1606×888 | Close-up: the Image Gen pane | close-up | — | none |
| `closeup-memory-health.png` | `#memory/health` | 1440×900, element | 2304×1525 | Close-up: the Memory area, Health tab | close-up | — | none |
| `closeup-model-catalog-rows.png` | `#models` | 1440×1300, element | 1596×2124 | Close-up: model catalogue rows | close-up | — | none |
| `closeup-notifications.png` | `#dashboard + bell` | 1440×900, element | 760×209 | Close-up: the notifications popover | close-up | — | none |
| `closeup-npu-array.png` | `#slots/npu` | 1440×900, element | 2304×637 | Close-up: NPU array band and activity | close-up | — | historical + illustrative |
| `closeup-npu-lanes.png` | `#slots/npu` | 1440×900, element | 2304×1696 | Close-up: NPU lanes (FLM and GGML) | close-up | — | historical + illustrative |
| `closeup-overview-agents.png` | `#dashboard` | 1440×900, element | 743×1260 | Close-up: Agents card with delegated runs | close-up | — | none |
| `closeup-overview-now-band.png` | `#dashboard` | 1440×900, element | 2298×262 | Close-up: the Now band (mode, GPU memory blocks) | close-up | — | historical + illustrative |
| `closeup-overview-serving-now.png` | `#dashboard` | 1440×900, element | 1527×1260 | Close-up: Serving now chart and per-slot table | close-up | requests-latency-widget.png (#3629, nearest) | historical + illustrative |
| `closeup-overview-usage.png` | `#dashboard` | 1440×900, element | 1326×857 | Close-up: Usage card (24 h requests, decode, TTFT) | close-up | — | historical + illustrative |
| `closeup-providers-tab.png` | `#models/providers` | 1440×900, element | 1596×370 | Close-up: Providers list | close-up | — | none |
| `closeup-settings-updates.png` | `#settings/updates` | 1440×900, element | 1848×1261 | Close-up: Settings, Updates panel | close-up, docs step | — | none |
| `closeup-slot-cards.png` | `#slots` | 1440×900, element | 1608×839 | Close-up: slot cards with lane codes and controls | docs step (support cards clip, see note) | slots-lifecycle.png (#3629, nearest) | historical |
| `closeup-slot-rail.png` | `#slots` | 1440×900, element | 638×1152 | Close-up: slot rail grouped by lane with state glyphs | close-up | — | historical |
| `closeup-status-dock.png` | `#slots (1100 wide)` | 1100×900, element | 2200×182 | Close-up: the status dock with the two-row ticker | close-up | — | none |
| `closeup-telemetry-gauges.png` | `#slots` | 1440×900, element | 2298×476 | Close-up: throughput, GPU, CPU/RAM and NPU gauges with the memory band | close-up | — | historical + illustrative |
| `dashboard-overview.png` | `#dashboard` | 1680×1050 | 3360×2100 | Overview: GPU memory band, Needs-you inbox, Serving now chart and table, Agents card | hero, feature section | dashboard-overview.png | historical + illustrative |
| `endpoints-tab.png` | `#slots/endpoints` | 1440×900 | 2880×1800 | Slots, Endpoints tab: local OpenAI-compatible endpoints with live tok/s | docs step | endpoints-tab.png (#3629) | historical |
| `hardware-metrics.png` | `#dashboard` | 1440×900, element | 938×574 | Close-up: Hardware card (GPU, CPU, NPU, RAM, disk) | close-up, docs step | hardware-metrics.png (#3629) | illustrative |
| `image-gen-comfyui.png` | `#slots/image` | 1440×900 | 2880×1800 | Slots, Image Gen tab: Generate pane on FLUX.1-schnell | feature section, docs step | image-gen-comfyui.png | illustrative |
| `logs-activity.png` | `#logs` | 1440×900 | 2880×1800 | Logs: the merged journal view | docs step | logs-activity.png (#3629) | none |
| `mcp-tab.png` | `#agent/mcp` | 1440×900 | 2880×1800 | Agents, MCP: hal0-admin, hal0-memory and hal0-agents servers, bridge clients | docs step | mcp-tab.png | none |
| `memory-agents.png` | `#memory/agents` | 1440×900 | 2880×1800 | Memory, Agents: who reads and writes memory | docs step | — | none |
| `memory-bank.png` | `#memory/facts` | 1440×900 | 2880×1800 | Memory, Facts: a bank with sources, filters, timeline and tag map | docs step | memory-bank.png | none |
| `memory-health.png` | `#memory/health` | 1440×900 | 2880×1800 | Memory, Health: "Nothing needs attention", engine, models and banks | feature section | memory-graph.png (nearest) | none |
| `memory-knowledge.png` | `#memory/knowledge` | 1440×900 | 2880×1800 | Memory, Knowledge: pages, mental models and the refresh policy | docs step | — | none |
| `memory-operations.png` | `#memory/operations` | 1440×900 | 2880×1800 | Memory, Operations: an empty queue | docs step | — | none |
| `models-flm.png` | `#models/flm` | 1440×900 | 2880×1800 | Models, FLM: the FastFlowLM catalogue for the NPU | docs step | — | none |
| `models-image.png` | `#models/image` | 1440×900 | 2880×1800 | Models, Image / ComfyUI: image catalogue with fit-on-this-box meter | feature section, docs step | — | none |
| `models-profiles.png` | `#models/profiles` | 1440×900 | 2880×1800 | Models, Profiles: seed launch profiles | docs step | profile-edit-drawer.png (nearest) | historical |
| `models-providers.png` | `#models/providers` | 1440×900 | 2880×1800 | Models, Providers: OpenRouter and a local OpenAI-compatible server | feature section, docs step | — | none |
| `models-registry.png` | `#models` | 1440×900 | 2880×1800 | Models, Inference Models: catalogue rows and the detail sidebar | feature section, docs step | models-registry.png | historical |
| `operator-board.png` | `#board` | 1440×900 | 2880×1800 | Operator Board: the Hermes kanban | feature section, docs step | operator-board.png | none |
| `phone-agents.png` | `#agent` | 390×844 | 780×1688 | Phone: Agents | feature section | — | illustrative |
| `phone-dock-open.png` | `#slots + dock` | 390×844 | 780×1688 | Phone: the dock expanded over the page | close-up | — | illustrative |
| `phone-memory-agents.png` | `#memory/agents` | 390×844 | 780×1688 | Phone: Memory Agents table as stacked cards | close-up | — | none |
| `phone-memory-health.png` | `#memory/health` | 390×844 | 780×1688 | Phone: Memory health | feature section | — | none |
| `phone-models.png` | `#models` | 390×844 | 780×1688 | Phone: Models catalogue | feature section | — | none |
| `phone-models-providers.png` | `#models/providers` | 390×844 | 780×1688 | Phone: Providers | feature section | — | none |
| `phone-more-drawer.png` | `#dashboard + More` | 390×844 | 780×1688 | Phone: the More drawer with the full navigation | close-up | — | none |
| `phone-overview.png` | `#dashboard` | 390×844 | 780×1688 | Phone: Overview with the bottom tab bar and one-line dock | hero (mobile), feature section | — | historical + illustrative |
| `phone-settings-updates.png` | `#settings/updates` | 390×844 | 780×1688 | Phone: Settings, Updates | docs step | — | none |
| `phone-slots.png` | `#slots` | 390×844 | 780×1688 | Phone: Slots | docs step (dock clips, see note) | — | historical + illustrative |
| `phone-slots-npu.png` | `#slots/npu` | 390×844 | 780×1688 | Phone: NPU tab, the open tab scrolled into view | feature section | — | historical + illustrative |
| `phone-slots-stacks.png` | `#slots/stacks` | 390×844 | 780×1688 | Phone: Stacks tab, deep-linked (tab strip scrolled to it) | close-up | — | none |
| `services-page.png` | `#services` | 1440×900 | 2880×1800 | Services: Open WebUI, ComfyUI, Hermes and Hindsight cards | docs step | services-page.png | none |
| `settings-accounts.png` | `#settings/accounts` | 1440×900 | 2880×1800 | Settings, Connected Accounts | docs step | — | none |
| `settings-agent-chat.png` | `#settings/agents` | 1440×900 | 2880×1800 | Settings, Agent Chat | docs step | — | none |
| `settings-capabilities.png` | `#settings/capabilities` | 1440×900 | 2880×1800 | Settings, AI Capabilities: TTS and STT | docs step | — | none |
| `settings-doctor.png` | `#settings/doctor` | 1440×900 | 2880×1800 | Settings, Doctor: all clear | docs step | — | none |
| `settings-hardware.png` | `#settings/hardware` | 1440×900 | 2880×1800 | Settings, Hardware & Runtimes: detected hardware and backends | docs step | — | none |
| `settings-loaded-models.png` | `#settings/slots` | 1440×900 | 2880×1800 | Settings, Loaded Models: default slots and runtime limits | docs step | — | none |
| `settings-memory.png` | `#settings/memory` | 1440×900 | 2880×1800 | Settings, Memory: engine and graph extraction | docs step | — | none |
| `settings-page.png` | `#settings` | 1440×900 | 2880×1800 | Settings, Overview: health, services, live hardware | docs step | settings-page.png | illustrative |
| `settings-security.png` | `#settings/security` | 1440×900 | 2880×1800 | Settings, Security & Access | docs step | — | none |
| `settings-storage.png` | `#settings/storage` | 1440×900 | 2880×1800 | Settings, Storage: the model store | docs step | — | none |
| `settings-updates.png` | `#settings/updates` | 1440×900 | 2880×1800 | Settings, Updates: channel, pins and About | docs step | — | none |
| `slot-detail.png` | `#slots/primary` | 1440×900, element | 1112×1800 | Close-up: the slot editor drawer (model, profile, tune pills) | close-up, docs step | slot-detail.png | none |
| `slots-inference.png` | `#slots` | 1440×900 | 2880×1800 | Slots, Inference tab: telemetry header, slot cards, slot rail | hero, feature section | slots-inference.png | historical + illustrative |
| `slots-npu.png` | `#slots/npu` | 1440×900 | 2880×1800 | Slots, NPU tab: array band, NPU duty, lanes activity | feature section | — | historical + illustrative |
| `slots-runner-images.png` | `#slots/runner-images` | 1440×900 | 2880×1800 | Slots, Runner Images tab: launch images and their status | docs step | — | illustrative |
| `stacks-tab.png` | `#slots/stacks` | 1440×900 | 2880×1800 | Slots, Stacks tab: the daily-driver stack | feature section, docs step | stacks-tab.png | none |
| `tablet-models.png` | `#models` | 834×1112 | 1668×2224 | Tablet: Models | feature section | — | none |
| `tablet-overview.png` | `#dashboard` | 834×1112 | 1668×2224 | Tablet: Overview | feature section | — | historical + illustrative |
| `tablet-slots.png` | `#slots` | 834×1112 | 1668×2224 | Tablet: Slots | feature section | — | historical + illustrative |

