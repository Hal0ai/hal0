// Models → Image: the ComfyUI file inventory.
//
// ComfyUI weight files are not model-registry rows, so this tab lists what is on
// disk under the ComfyUI models tree (GET /api/comfyui/files), grouped by component
// kind, with the catalog models that load each file. A file nobody in the catalog
// owns (an operator's own checkpoint or LoRA) can be deleted from here, behind a
// confirm. A catalog-owned file is removed by removing its model, and a symlink is
// removed through its target — the server refuses both, and the detail pane says so
// instead of offering a button that can only fail.
import { useMemo, useState } from 'react'
import { fmtBytes } from '@/api/hooks/useModels'
import { useComfyuiFiles, useComfyuiFileDelete, useComfyuiModels } from '@/api/hooks/useComfyui'
import { ConfirmDialog } from '@/dash/primitives.jsx'
import { groupFilesByKind, kindLabel } from './image-files-pure'

const fileKey = (f) => `${f.subdir}/${f.relpath}`
const baseName = (relpath) => relpath.split('/').pop()
// Where the file sits, as the listing reports it: `loras/styles` for `styles/a.safetensors`.
const folderOf = (f) => [f.subdir, ...f.relpath.split('/').slice(0, -1)].join('/')

const note = (msg, kind) => window.__hal0Toast && window.__hal0Toast(msg, kind)

// What deletes a file a catalog model loads. Install and remove are CLI/REST only until the
// dashboard grows a Models tab, so the note names the command. A file two models load is kept
// by a plain remove (the other model still needs it): `--include-shared` deletes it anyway.
const ownedNote = (ids) =>
  ids.length === 1
    ? `Remove the model to delete this file: hal0 comfyui models remove ${ids[0]}`
    : `${ids.join(', ')} load this file. To delete it: hal0 comfyui models remove ${ids[0]} --include-shared (the others stop working).`

export function ImageFilesTab() {
  const filesQuery = useComfyuiFiles()
  const modelsQuery = useComfyuiModels()
  const del = useComfyuiFileDelete()
  const [selKey, setSelKey] = useState(null)
  const [confirmFile, setConfirmFile] = useState(null)

  const files = filesQuery.data?.files
  const root = filesQuery.data?.root
  const groups = useMemo(() => groupFilesByKind(files ?? []), [files])
  const labelOf = useMemo(() => {
    const byId = new Map((modelsQuery.data?.models ?? []).map((m) => [m.id, m.label]))
    return (id) => byId.get(id) || id
  }, [modelsQuery.data])

  // The detail pane never opens empty: with nothing picked (or the picked file
  // gone after a delete) it shows the first row on screen.
  const all = groups.flatMap((g) => g.files)
  const selected = all.find((f) => fileKey(f) === selKey) ?? all[0] ?? null

  const usedBy = (f) => f.models.map(labelOf).join(', ')

  const onConfirmDelete = async () => {
    const f = confirmFile
    if (!f) return
    try {
      await del.mutateAsync({ subdir: f.subdir, relpath: f.relpath })
      note(`Deleted ${f.relpath}`, 'ok')
    } catch (e) {
      note(`Delete failed — ${e?.message || 'see logs'}`, 'err')
    } finally {
      setConfirmFile(null)
    }
  }

  if (filesQuery.isPending) {
    return <div style={{ padding: 16, fontFamily: 'var(--jbm)', fontSize: 11, color: 'var(--fg-4)' }}>Loading files…</div>
  }
  if (filesQuery.isError) {
    return (
      <div style={{ padding: 16, fontFamily: 'var(--jbm)', fontSize: 11, color: 'var(--err)' }}>
        {filesQuery.error?.message || 'Failed to load files'}
      </div>
    )
  }
  if (all.length === 0) {
    return (
      <div className="mdl-list" style={{ marginTop: 18 }}>
        <div style={{ padding: 24, textAlign: 'center', fontFamily: 'var(--jbm)', fontSize: 12, color: 'var(--fg-4)' }}>
          <div>No image model files on disk yet.</div>
          <div data-testid="imgf-empty-hint" style={{ marginTop: 6 }}>
            Install one with <code>hal0 comfyui models install &lt;id&gt;</code>
            {' '}(<code>hal0 comfyui models list</code> shows the ids).
          </div>
        </div>
      </div>
    )
  }

  const totalBytes = all.reduce((n, f) => n + (f.size_bytes || 0), 0)

  return (
    <div className="models-layout" style={{ marginTop: 18 }}>
      <div className="mdl-list">
        <div className="mdl-list-h">
          <span>Image / ComfyUI files</span>
          <span className="ct">· {all.length} shown</span>
          <span className="right mono">{fmtBytes(totalBytes)} on disk</span>
        </div>
        {groups.map((g) => (
          <div key={g.kind} data-testid={`imgf-group-${g.kind}`}>
            <div className="mdl-section-label">{`${g.label} · ${g.files.length}`}</div>
            {g.files.map((f) => {
              const key = fileKey(f)
              return (
                <div
                  key={key}
                  className={'mdl-row' + (selected && fileKey(selected) === key ? ' sel' : '')}
                  data-testid={`imgf-row-${key}`}
                  style={{ gridTemplateColumns: 'minmax(0, 1fr) auto 78px' }}
                  onClick={() => setSelKey(key)}
                >
                  <span className="nm">
                    {baseName(f.relpath)}
                    <span className="sub">{folderOf(f)}</span>
                  </span>
                  <span className="mdl-row-tags">
                    {f.symlink && <span className="chip" title="This file is a symlink">link</span>}
                    {f.models.length > 0 ? (
                      <span className="chip ok" title={`Loaded by ${usedBy(f)}`}>Used by {usedBy(f)}</span>
                    ) : (
                      <span className="chip warn" title="No catalog model loads this file">Unmanaged</span>
                    )}
                  </span>
                  <span className="sz num">{fmtBytes(f.size_bytes)}</span>
                </div>
              )
            })}
          </div>
        ))}
      </div>

      <div className="models-sidebar">
        {selected && (
          <div className="mdl-detail" data-testid="imgf-detail">
            <div className="mdl-detail-h">
              <div className="nm">{baseName(selected.relpath)}</div>
              <div className="repo">{folderOf(selected)}</div>
            </div>
            <div className="mdl-detail-meta">
              <div style={{ gridColumn: '1 / -1' }}>
                <div className="k">Path</div>
                <div className="v" style={{ wordBreak: 'break-all' }}>
                  {root ? `${root.replace(/\/+$/, '')}/${fileKey(selected)}` : fileKey(selected)}
                </div>
              </div>
              <div>
                <div className="k">Size</div>
                <div className="v">{fmtBytes(selected.size_bytes)}</div>
              </div>
              <div>
                <div className="k">Kind</div>
                <div className="v">{kindLabel(selected.kind)}</div>
              </div>
              <div style={{ gridColumn: '1 / -1' }}>
                <div className="k">Used by</div>
                <div className="v">
                  {selected.models.length > 0 ? usedBy(selected) : 'Unmanaged — no catalog model loads this file'}
                </div>
              </div>
              {selected.arch && (
                <div>
                  <div className="k">Family</div>
                  <div className="v">{selected.arch}</div>
                </div>
              )}
              {selected.api_id && (
                <div>
                  <div className="k">API model id</div>
                  <div className="v" style={{ wordBreak: 'break-all' }}>{selected.api_id}</div>
                </div>
              )}
            </div>
            <div style={{ padding: '14px 16px', fontFamily: 'var(--jbm)', fontSize: 11.5, color: 'var(--fg-3)', lineHeight: 1.5 }}>
              {selected.models.length > 0 ? (
                <span data-testid="imgf-owned-note">{ownedNote(selected.models)}</span>
              ) : selected.symlink ? (
                <span data-testid="imgf-symlink-note">
                  This file is a link. hal0 does not delete through a link: delete its target instead.
                </span>
              ) : (
                <button
                  className="btn sm danger"
                  data-testid="imgf-delete"
                  disabled={del.isPending}
                  onClick={() => setConfirmFile(selected)}
                >
                  Delete…
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={!!confirmFile}
        destructive
        onCancel={() => setConfirmFile(null)}
        onConfirm={onConfirmDelete}
        confirmDisabled={del.isPending}
        title={`Delete ${confirmFile?.relpath ?? ''}?`}
        message={
          <span>
            This removes{' '}
            <span className="mono" style={{ color: 'var(--fg)', wordBreak: 'break-all' }}>
              {confirmFile ? fileKey(confirmFile) : ''}
            </span>{' '}
            ({confirmFile ? fmtBytes(confirmFile.size_bytes) : ''}) from disk. No catalog model loads it.
          </span>
        }
        confirmLabel="Delete file"
      />
    </div>
  )
}
