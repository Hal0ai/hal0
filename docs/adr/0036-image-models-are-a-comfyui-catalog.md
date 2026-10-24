# ADR-0036: Image models are a ComfyUI-owned catalog of typed components

## Status

Accepted (2026-10-24). The image-generation overhaul that this record governs
lands as five pull requests, each leaving the product working: the catalog,
the path owner and the installer first; then generation from the catalog;
then the separation of the LLM registry; then the dashboard; then an opt-in
plugin manager. This PR carries the record and decisions 1 to 5: the catalog,
its data model and component rule, removal, and the path owner. Decision 6
describes the end state the later pull requests build.

It does not amend an earlier record. ADR-0026 (a model delete removes its
files) keeps governing LLM models in the registry; this record keeps image
files out of that path.

## Context

hal0 has two image systems that never meet, and both leak into the LLM
registry.

- **The curated path.** `/v1/images/generations` and the Open WebUI wiring
  accept only curated single-file checkpoints, through two simple workflow
  templates. Larger models, such as Qwen-Image or the Wan 2.2 video models,
  are not single files. They are a set of weights (a diffusion model,
  a text encoder, a VAE, often a LoRA) that a workflow loads together.
- **The ComfyUI packs.** Those larger models are fetched by vendored shell
  scripts and used only from inside ComfyUI's own editor. The fetch has no
  status endpoint, keeps its jobs in memory so a restart loses them, and
  nothing in the dashboard calls it.
- **The registry leak.** One scanner handles LLMs and image files. Its skip
  list drops some ComfyUI accessory directories, `diffusion_models/` among
  them (`registry/discover.py`), so the main weights of those models are
  invisible. It does not drop `unet/` or `latent_upscale_models/`, so files
  there register as standalone chat-capable models. ComfyUI files are
  recognised elsewhere by a substring match on the path
  (`comfyui_category` in `services/models_service.py`).
- **Several owners of one path.** The ComfyUI data root was computed inside
  the provider's `container_spec`. The API routes read three environment
  variables (`COMFYUI_MODELS_DIR`, `COMFYUI_WORKFLOWS_DIR`, `COMFYUI_DATA_DIR`),
  each with its own `/mnt/ai-models` default. The pull engine derived a third
  copy from the model store and ignored `HAL0_COMFYUI_DATA_ROOT`. Setting that
  override moved the container's mounts but not where a pull wrote, and the
  three route variables could each point somewhere else again.

The shared cause is that nothing owns an image model. The registry's unit is
one servable model with one set of capabilities. An image model is several
typed files that other image models can share.

## Decision

Image models are a catalog of typed components that the ComfyUI module owns.
The LLM registry owns no ComfyUI file.

### 1. The catalog lives in `hal0.comfyui.catalog`

The catalog is Python frozen dataclasses, in the style of
`registry/curated.py`. It replaces `hal0/comfyui/capabilities.py` and every
image entry in `registry/curated.py`. One module is the source for the
dashboard, the installer, the API and the editor workflows.

### 2. The data model

A **component** is one weight file. Its kind decides which ComfyUI
subdirectory it lives in:

| Kind | Subdirectory |
|---|---|
| `checkpoint` | `checkpoints` |
| `diffusion_model` | `diffusion_models` |
| `text_encoder` | `text_encoders` |
| `vae` | `vae` |
| `lora` | `loras` |
| `upscaler` | `upscale_models` |
| `clip_vision` | `clip_vision` |
| `latent_upscaler` | `latent_upscale_models` |

A component carries its path under that subdirectory (which may be nested, as
Wan's LoRAs are), its kind, a source (a Hugging Face repo and file; components
are Hugging Face only), and an approximate size.

An **image model** has an id and a label, a task (`txt2img`, `img2img`,
`txt2video`, `img2video` or `upscale`), a tuple of components, the workflow
template that uses them, a flag for whether the API can render it, its sizes
(the first is native), a time estimate, a licence and notes. Which model to
highlight as the starter is a query, `recommended_model_id(pool_gb)`, that
picks by the box's memory pool rather than a flag on an entry.

Install state is `none`, `partial` or `complete`, decided by whether the
component files exist. It is never stored, so a file an operator adds or
removes by hand is reflected the next time anyone asks.

### 3. The component rule

An entry's components are exactly the weight files its template references:
every string input ending in `.safetensors`, `.pth`, `.ckpt`, `.pt` or `.gguf`.
Files the old scripts pulled that no template loads are dropped. Two
components with the same destination must share one source, so a shared VAE
is one file with one origin.

### 4. Removal respects sharing and the operator's own files

Removing an image model deletes a component file only when no other catalog
model in state `complete` references it. Files outside the catalog are never
deleted as a side effect of removing a model. They go only through an
explicit file-level delete, which refuses catalog-owned files, path traversal
and symlinks.

### 5. One owner for the paths

`hal0.comfyui.paths` owns every ComfyUI data path:

- `data_root()` is `HAL0_COMFYUI_DATA_ROOT` when set and non-empty, else the
  configured model store's `comfyui` directory.
- `models_root()`, `workflows_dir()`, `custom_nodes_dir()` and
  `extra_model_paths_file()` are fixed children of the data root.
- `SUBDIRS` names every model subdirectory hal0 mounts: the eight kind
  subdirectories above plus `unet`, `clip`, `controlnet`, `embeddings` and
  `style_models`. The canonical `extra_model_paths.yaml` is rendered from it.
- `is_under_models_root(path)` resolves both sides and compares path
  components, never string prefixes.

The provider's container spec, the API routes and the pull engine derive
their paths from these functions. `COMFYUI_MODELS_DIR`, `COMFYUI_WORKFLOWS_DIR`
and `COMFYUI_DATA_DIR` are removed, with no fallback: `HAL0_COMFYUI_DATA_ROOT`
moves the whole tree or nothing does. `COMFYUI_BASE_URL` stays, because it
names an endpoint, not a path.

The provider provisions the data root each time it renders the container spec,
which the slot-drift probe also does every 30 seconds. It creates the mounted
directories on every render: that is cheap, silent when they exist, and podman
refuses a bind mount whose source is absent. It writes the canonical
`extra_model_paths.yaml` if that file is absent or empty, and syncs the GPU
gate custom node from a package asset (staged beside the target and renamed
into place), at most once per process per data root. A write that fails is
retried on a later render, so fixing a permission heals without a restart, but
it logs once. An empty YAML hides every model from ComfyUI, and today the
provider writes an empty one whenever the installer's copy is missing. The
installer stops copying both, and no longer creates the ComfyUI tree.

### 6. The registry stops seeing ComfyUI files

- Discovery skips the whole `models_root()` subtree, by a resolved-path check
  and not a substring match.
- Adding a model by a path under that tree is refused with 422
  `model.image_asset`.
- `comfyui_category` and the ComfyUI self-heal leave the model listing.
- One SQL migration deletes the registry rows that point at ComfyUI paths or
  carry the `image` capability. It deletes rows only, never files.

## Options considered

- **Keep image models in the registry, with a richer row that links its
  components.** Rejected. A registry model is one servable thing with its own
  capabilities, profile and delete semantics (ADR-0026 removes that model's
  files). A component is shared between image models, so deleting one model
  must not delete what another still needs, and no field on a registry row
  can say that without the registry learning about ComfyUI layouts.
- **Store install state in the database or a manifest.** Rejected. The files
  already hold that fact. A second copy drifts the first time an operator
  drops in or deletes a checkpoint by hand, and then something has to
  reconcile the two.
- **Keep the three per-directory overrides next to the new root.** Rejected.
  Three variables that can disagree is the defect this record fixes, and
  keeping them as a fallback keeps a second owner alive.
- **Keep the old `(capability, family)` pairs, the shell scripts and the fetch
  route alive beside the catalog.** Rejected. hal0 builds the new system and
  converts what exists once, rather than supporting both.

## Consequences

- The dashboard, the installer, the generation route and the editor workflows
  read one table, so a model's files, its workflow and its sizes cannot
  disagree.
- Moving the model store, or setting `HAL0_COMFYUI_DATA_ROOT`, moves the
  container mounts, the workflow listing, the inventory and every pull
  together. Boxes that set one of the three removed variables must switch to
  `HAL0_COMFYUI_DATA_ROOT`; the old names are no longer read.
- Shared components are installed once and removed only when the last
  complete model that uses them is removed. Files the operator added are
  never touched by a model removal.
- Image files no longer appear in the LLM registry, the chat model pickers or
  the LLM drawer, and an image GGUF added by path is refused instead of being
  registered as a chat model. The migration drops existing image rows without
  touching the files; the catalog re-derives their state from disk.
- GGUF image models need a custom node in the runner image, so they stay out
  of the catalog until the runner carries it. The two curated GGUF image
  entries are dropped rather than left broken.

## References

- ADR-0026 (model delete removes files), ADR-0029 (profile-centric config;
  one owner per fact).
- `src/hal0/comfyui/paths.py`, `src/hal0/providers/comfyui.py`
  (`container_spec`), `src/hal0/api/routes/comfyui.py`,
  `src/hal0/registry/pull.py` (`_comfyui_models_dir`),
  `src/hal0/registry/discover.py`, `src/hal0/services/models_service.py`
  (`comfyui_category`), `src/hal0/registry/curated.py`.
