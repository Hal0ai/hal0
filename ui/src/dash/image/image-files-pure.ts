// Models → Image: grouping and naming for the ComfyUI file inventory. Pure, so the
// section order is tested without rendering (__tests__/image-files-pure.test.ts).
//
// `kind` is what GET /api/comfyui/files reports (comfyui/inventory.py): a catalog
// ComponentKind for the directories the catalog installs into, and the
// directory's own name for one operators fill by hand (controlnet, embeddings…).

import type { ComfyuiFile } from '@/api/hooks/useComfyui'

// The catalog kinds, in the order an operator reads a ComfyUI install: the main
// weights first, then the parts a pipeline loads beside them. The label is the
// section header. Both come from here and nowhere else.
const KINDS: ReadonlyArray<{ kind: string; label: string }> = [
  { kind: 'checkpoint', label: 'Checkpoints' },
  { kind: 'diffusion_model', label: 'Diffusion models' },
  { kind: 'text_encoder', label: 'Text encoders' },
  { kind: 'vae', label: 'VAE' },
  { kind: 'lora', label: 'LoRAs' },
  { kind: 'upscaler', label: 'Upscalers' },
  { kind: 'clip_vision', label: 'CLIP vision' },
  { kind: 'latent_upscaler', label: 'Latent upscalers' },
]

const KIND_LABEL = new Map(KINDS.map((k) => [k.kind, k.label]))

// `style_models` -> "Style models"; a name the table does not know still reads.
export function kindLabel(kind: string): string {
  const known = KIND_LABEL.get(kind)
  if (known) return known
  const words = kind.replace(/_/g, ' ').trim()
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : kind
}

export interface FileGroup {
  kind: string
  label: string
  files: ComfyuiFile[]
}

// Catalog kinds in their fixed order, then every other kind alphabetically. A
// kind with no files is omitted. Files keep the order the listing sent (it is
// already sorted by subdirectory, then path).
export function groupFilesByKind(files: readonly ComfyuiFile[]): FileGroup[] {
  const byKind = new Map<string, ComfyuiFile[]>()
  for (const file of files) {
    const group = byKind.get(file.kind)
    if (group) group.push(file)
    else byKind.set(file.kind, [file])
  }
  const known = KINDS.map((k) => k.kind)
  const extras = [...byKind.keys()].filter((k) => !KIND_LABEL.has(k)).sort()
  return [...known, ...extras]
    .filter((kind) => byKind.has(kind))
    .map((kind) => ({ kind, label: kindLabel(kind), files: byKind.get(kind)! }))
}
