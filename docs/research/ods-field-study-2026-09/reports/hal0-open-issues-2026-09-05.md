# hal0 open issues (106) as of 2026-09-05 — hal0ai/hal0

| # | title | labels |
|---|-------|--------|
| 1537 | install: on Ubuntu 26.04 (python3.14) the Hindsight engine installs with --ignore-requires-python | ready-for-human, backlog |
| 2234 | providers: [slots].default_images[family] tier is consulted only by the llama/container path — non-llama providers skip it | needs-triage |
| 2228 | tests: comfyui phase4 tests patch the shared os.path.isfile — same process-wide hazard that caused #2166 | needs-triage |
| 2221 | slots: last_crash_line stays None when the container dies with a usage error (exit 64) — the crash-line extractor only recognizes model-load faults | needs-triage |
| 2168 | chore: execute the v1.1.0 sunset backlog (re-stamped to v1.2.0 at the cut) | needs-triage |
| 2216 | install: LXC with /dev/kfd forwarded seeds every slot gpu-vulkan — derive_device's ROCm lane depends on rocm-smi being installed | |
| 2212 | models: verify-files / re-pull affordance for model + mmproj sidecar | ready-for-agent |
| 2002 | flaky: test_warming_slot_recovers_when_stale races STARTING→READY on CI | needs-triage |
| 2203 | models: seed-profile route emits changed_fields unconditionally | needs-triage |
| 2202 | api tests: promote crud app fixtures to tests/api/conftest.py | needs-triage |
| 2201 | models: runs_on derivation reads hardware info per row per poll | needs-triage |
| 2200 | models: comfyui self-heal applies on list but not single GET | needs-triage |
| 2195 | Profile apply preview bills added/changed flags only — removed flags not mentioned | needs-triage |
| 2192 | Omni tool eligibility never sees tts/stt/embed slots: modality facts missing on real deployments | bug, needs-triage |
| 2191 | OmniRouter generate_image self-deadlocks the caller LLM on single-GPU hosts (gpu.image_mode 503 mid-loop) | bug, needs-triage |
| 2190 | _runtime_family does not fold runner aliases when classifying by profile.runner | needs-triage |
| 2184 | Curated install slots reference demoted seed profiles — fresh box materializes them as custom profiles | needs-triage |
| 2180 | slot migrate-flags aborts whole run on a slot whose default model is not in the registry | needs-triage |
| 2178 | Backfill Model.architecture for pre-existing registry rows | needs-triage |
| 2169 | ci: cut PR wall-clock from ~30 min to ~10 min (xdist, path gating, tiering) | enhancement, needs-triage |
| 2164 | agent install pi via API path fails: daemon uid 996 cannot npm install -g (needs Hermes-style CLI-local provision) | needs-triage |
| 2101 | release: GA delivery checklist — the stable pointer, the releases proxy's 10-release window, and the live installer mirror | bug |
| 2155 | memory migrate unify --apply broken against pinned engine 0.9.2: sync document export returns HTTP 410 | bug, needs-triage |
| 2153 | docs: guide for pointing external coding agents at the hal0 memory engine (bank naming, monorepos, migration off per-agent plugins) | documentation, needs-triage |
| 2154 | feat(memory): bank hygiene surface — list empty/stale/foreign banks and offer guided cleanup | enhancement, needs-triage |
| 2118 | Retire the upstream runner-image variant once ROCmFPX syncs qwen4exp | slots, upstream-drift |
| 2111 | docs-sync: fragment anchors are passed through unchanged and never resolve on the forum | bug, documentation, needs-triage |
| 2108 | brain: tool_model has no UI path, and its default has no live target on a fresh install | enhancement, needs-triage |
| 1530 | release: the stable channel pointer is still 0.9.8 — tagging v1.0.0 does not deliver it to a single existing user | ready-for-human |
| 2096 | hal0 update should restart slots by default when the release rolls the runner image — a stale image keeps serving the build the release replaced | enhancement |
| 1823 | Model capability labels live in a hand-curated web file, not the registry — public leaderboard shows 10/26 models with caps instead of 18 | backlog |
| 1841 | polish(ui): v1.0.0-rc.5 validation sweep — 3 items | ready-for-agent, backlog |
| 2028 | GET /api/services/health is a static three-branch construction and can never include hindsight — the dashboard footer's readiness count structurally cannot move on a hindsight-api outage | bug, ready-for-agent, needs-triage |
| 2019 | dev-deploy: pip install to a live box leaves privileged wrappers stale — write-quadlet exits 64 on new quadlet keys | needs-triage |
| 2017 | hermes memory plugin creates one uuid4 document per turn — no session_id in metadata | needs-triage |
| 2016 | memory: no document-ingest endpoint — Sources 'Ingest' button is permanently disabled | needs-triage |
| 2011 | Cross-backend profile switching (#1636) is dead code on real installs — no seed profile declares a backend | needs-triage |
| 2003 | docs-discourse-sync: reconcile forum topics for deleted/moved docs | enhancement |
| 1995 | memory v2 mock graph payload speaks a normalized dialect the wire never uses | needs-triage |
| 1997 | memory v2: no e2e coverage of the slab-truncation notice (needs a truncated:true mock bank) | needs-triage |
| 1996 | memory v2 web view: tag-chip dimming keys on a node field that doesn't exist upstream | needs-triage |
| 1994 | no toast when an agent-requested delete lands in the approval queue | needs-triage |
| 1993 | memory v2: no operation-retry affordance (v3 test coverage dropped) | needs-triage |
| 1990 | memory: _VALID_FACT_TYPES hardcoded allowlist is a drift trap — server closes a type set the client types open-ended | bug |
| 1989 | HindsightRestClient.list_memories comma-joins multi-type into a silently-empty upstream filter | needs-triage |
| 1070 | hal0-quantize: validate & promote experimental ROCmFPX variant | enhancement, backlog |
| 1319 | Feature Request: Integrate llama-ai and CachyLLama | backlog |
| 1349 | Field results from a Strix Halo box: 5 published ROCmFPX/STRIX_LEAN quant repos, MXFP4 requant win, FP4+DFlash at 96-112 tok/s | backlog |
| 1422 | fix(api): GET /api/slots returns duplicate entries with the same slot id when a name-keyed and an id-keyed TOML coexist | ready-for-agent, backlog |
| 1428 | fix(metrics): slot_sample writer fails several times per second with UNIQUE constraint failed on (ts, slot_id), dropping samples and flooding the log | ready-for-agent, backlog |
| 1445 | api: typed request bodies — audit follow-through | ready-for-agent, backlog |
| 1477 | polish: low-priority sweep list — 31 items | ready-for-agent, backlog |
| 1502 | ci: the scar ratchet counts prose, so accurate documentation trips it | ready-for-agent, backlog |
| 1522 | logs: raw journald follow-streams send no keep-alive, so a quiet slot's log drawer is reaped by any proxy | ready-for-agent, backlog |
| 1528 | polish(agent-cli): v1.0 GA sweep — 5 items | ready-for-agent, backlog |
| 1756 | seam: quadlet allow-list follow-ups (F2–F6 from #1748 review) | ready-for-agent, backlog |
| 1783 | chore(ui): converge dashboard tables on the shared .dtable idiom | needs-triage, backlog |
| 1821 | test(slots): nothing asserts that a spec-designated slot-owned flag is actually implemented and reaches argv | ready-for-agent, slots, backlog |
| 1825 | build_roster collapses unrelated models: 8 distinct models share the gguf basename model.gguf | backlog |
| 1925 | validation: A/B the non-AMD Vulkan lanes (NVIDIA-without-CDI, Intel iGPU) on the pinned rocmfpx runner | needs-triage, backlog |
| 1928 | ui: memory ruler header and bar derive "free" from different bases (model sum vs raw GTT) — same-screen self-contradiction survives #1900 | needs-triage, backlog |
| 1929 | ui: memory-map GTT-cap fallback reads dead keys (rawHw.unified_memory_mb, stats gtt_total_mb) — pool silently becomes system RAM while labelled "GPU pool (GTT)" | needs-triage, backlog |
| 1967 | slot-config: save_slot_config silently drops keys on round-trip (output_sanity, type) — lossy API with zero callers waiting to trap the next one | bug, backlog |
| 1948 | runner: restore a correct Vulkan backend and refresh upstreams | ready-for-agent |
| 1984 | gpu: _vulkan_lane_is_loadable looks through a stale pin — if the retag pass ever throws, the slot sits on gpu-vulkan holding the #1888 image | bug |
| 1983 | gpu: hal0-gpu-perms.service Before=hal0.target does not order it ahead of slot units — the boot converge can lose the race it exists to win | bug |
| 1974 | capabilities: _flm_image_present reads the rootless store and caches False — a broken podman permanently drops the NPU backend from /api/capabilities | bug |
| 1969 | slots: nothing pins llama-server's LOADING state, so the /health→/v1/models fallback could hand the output-sanity gate a 503 | needs-triage |
| 1966 | gpu: ComfyUI has no reachable lane on a kfd-less AMD box (picker offers gpu-vulkan only, no cpu row/profile) | needs-triage |
| 1829 | brain chat: every board tool 401s on a fresh install — the pinned hermes wheel ships no web_dist, so the session token can never be harvested | ready-for-human |
| 1947 | runtime: support CIRU vLLM distributions (jcbtc/Ling-3.0-Flash-CIRU-int4-Strix-native) — new vLLM runtime family or managed-upstream integration | needs-triage |
| 1936 | runner ade07ba: segfault (SIGSEGV) at model load when the container has zero GPU devices mapped — crashes before HTTP binds | needs-triage |
| 1932 | agents: anchor_window ceiling diagnosis misses id-keyed slot TOMLs (read_slot_ceiling assumes .toml) | needs-triage |
| 1931 | memory: extraction preflight trusts wrapper.extraction_slot (configured intent), not the live hindsight-api drop-in | needs-triage |
| 1930 | memory: MCP memory_add path has no extraction-ctx preflight — #1903 defect is identical on /mcp/memory | needs-triage |
| 1840 | polish(cli/api): v1.0.0-rc.5 validation sweep — 13 items | ready-for-agent |
| 1833 | memory: hal0 memory status reports 'Writes landing' on a store that has never landed a fact — the verdict only watches the failed counter for growth | ready-for-agent |
| 1834 | memory: hindsight extraction is wired to a shared slot with no concurrency cap, no max_tokens and a requeueing 300s x4 ladder — the retain queue never drains on a CPU-only install | ready-for-human |
| 1867 | hermes: an upgraded box keeps an old slot ceiling below Hermes' floor, and nothing preflights the anchor window | ready-for-human |
| 1873 | brain: the self-call clients carry the same bare-float timeout defect #1832 fixed elsewhere, and one truncates slot lifecycle waits | ready-for-agent |
| 1870 | cli(slots): lifecycle verbs wait up to 966s with no progress output, and nothing bounds a wedged call | needs-triage |
| 1869 | slots: bound the systemd/podman start subprocesses so lifecycle waits are finite server-side | needs-triage |
| 1868 | install: static slot seeds are copied verbatim with no hardware budget, so every box now warms the brain slot at 65536 | needs-triage |
| 1862 | capacity: a stale or missing hardware.json makes a real GPU box book VRAM as RAM | |
| 1858 | api(slots): /api/slots/by-name/{name} and /by-id/{id} return an unenriched payload despite promising parity with /api/slots/{name} | needs-triage, slots |
| 1859 | api(slots): the llama-only context resolver is applied to FLM/Kokoro/Moonshine/Qwen3-TTS/ComfyUI slots, manufacturing an 8192 window they never run with | needs-triage, slots |
| 1844 | update: hal0 update never refreshes /usr/local/bin/hal0, so the CLI on PATH keeps running the OLD release after an in-place upgrade | ready-for-agent |
| 1845 | update: the 'Convergence incomplete' panel prints a remediation that cannot work — the migration refuses while slot units are up and the panel never mentions --stop-services | ready-for-agent |
| 1822 | install/doctor: a LAN bind with auth off is never surfaced to the operator | needs-triage |
| 1820 | slots: the reconciler detects the netavark port black hole and parks it in ERROR — nothing owns the repair | needs-triage, slots |
| 1552 | ui(a11y): Menu primitive's first real call site has no keyboard support — no Escape, no arrow-nav, no menu roles | ready-for-agent |
| 1550 | deploy: scripts/deploy.sh reports success while the backend keeps serving the old code | needs-triage |
| 1429 | fix(slots): slot quadlets hard-code LogDriver=none, so podman logs and the slot log endpoints have nothing to show | needs-triage |
| 1421 | fix(slots): POST /api/slots writes name-keyed TOML/unit/container on an id-keyed box — re-creates the pre-migration layout and lets the id-keying migration clobber live configs | ready-for-human |
| 1426 | fix(slots): hal0-systemctl has no unmask verb — a masked slot unit is unrecoverable through the API and reports an undiagnosable error | ready-for-human |
| 1545 | repo: every PR conflicts on CHANGELOG.md — consider changelog fragments | ready-for-human |
| 1536 | polish(board/palette): v1.0 GA sweep — 3 items on two previously unaudited surfaces | ready-for-agent |
| 1529 | polish(journal/logs): v1.0 GA sweep — 3 items | ready-for-agent |
| 1525 | polish(upstreams): v1.0 GA sweep — 4 items | ready-for-agent |
| 1524 | polish(stacks): v1.0 GA sweep — 6 items | ready-for-agent |
| 1521 | activity: retention prune runs only at boot — activity.db grows unbounded on a long-lived hal0-api | ready-for-agent |
| 1519 | agent-cli: uninstall prints 'Uninstalled' on a 207 partial uninstall — residual files are never surfaced | ready-for-agent |
| 1512 | stacks: import commit never verifies the envelope checksum — only dry_run reports checksum_ok | ready-for-agent |
| 1511 | stacks: applying a stack unloads every untouched running slot, and the confirm dialog never says so | ready-for-agent |
| 1436 | fix(slots): four more artefact names are still derived from the slot NAME outside the naming seam — logs, reset-failed, NPU column probe | bug |
| 1249 | Track upstream: hermes-agent Python 3.14 support (file at NousResearch/hermes-agent) | bug, ready-for-human |
