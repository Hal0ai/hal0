# provision_comfyui_downloads

> 23 nodes · cohesion 0.13

## Key Concepts

- **provision_comfyui_downloads()** (11 connections) — `src/hal0/comfyui/provision.py`
- **resolve_variants()** (7 connections) — `src/hal0/comfyui/provision.py`
- **test_provision.py** (7 connections) — `tests/comfyui/test_provision.py`
- **_FakeClock** (7 connections) — `tests/comfyui/test_provision.py`
- **provision.py** (6 connections) — `src/hal0/comfyui/provision.py`
- **estimate_totals()** (4 connections) — `src/hal0/comfyui/provision.py`
- **ProvisionResult** (4 connections) — `src/hal0/comfyui/provision.py`
- **test_estimate_totals_sums_size_and_time()** (3 connections) — `tests/comfyui/test_provision.py`
- **test_no_activation_when_every_fetch_fails()** (3 connections) — `tests/comfyui/test_provision.py`
- **test_queues_fetch_and_activates_on_first_landed()** (3 connections) — `tests/comfyui/test_provision.py`
- **_activate_img_slot()** (2 connections) — `src/hal0/comfyui/provision.py`
- **.sleep()** (2 connections) — `tests/comfyui/test_provision.py`
- **test_empty_selection_is_a_noop()** (2 connections) — `tests/comfyui/test_provision.py`
- **test_resolve_variants_maps_and_collects_unknown()** (2 connections) — `tests/comfyui/test_provision.py`
- **WS-G (#1113): drive the ComfyUI per-variant download + img-slot activation.  The** (1 connections) — `src/hal0/comfyui/provision.py`
- **Queue the working per-variant fetch for every pick, then wait — activating     t** (1 connections) — `src/hal0/comfyui/provision.py`
- **Outcome of a ComfyUI download provisioning run.** (1 connections) — `src/hal0/comfyui/provision.py`
- **Map ``(capability_id, family)`` picks to variants.      Returns ``(variants, unk** (1 connections) — `src/hal0/comfyui/provision.py`
- **Total ``(approx_gb, est_seconds)`` across *variants* — the picker/review     'th** (1 connections) — `src/hal0/comfyui/provision.py`
- **Bring the ComfyUI img slot live (enable ``hal0-slot@img.service``).      Delegat** (1 connections) — `src/hal0/comfyui/provision.py`
- **.__init__()** (1 connections) — `tests/comfyui/test_provision.py`
- **WS-G (#1113): ComfyUI per-variant download driver + img-slot activation.** (1 connections) — `tests/comfyui/test_provision.py`
- **Injectable sleep that advances a step counter so poll loops progress.** (1 connections) — `tests/comfyui/test_provision.py`

## Relationships

- [ModelVariant](ModelVariant.md) (4 shared connections)
- [proxy_board_events](proxy_board_events.md) (2 shared connections)
- [Persona](Persona.md) (1 shared connections)
- [build_auto_selections](build_auto_selections.md) (1 shared connections)

## Source Files

- `src/hal0/comfyui/provision.py`
- `tests/comfyui/test_provision.py`

## Audit Trail

- EXTRACTED: 53 (74%)
- INFERRED: 19 (26%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*