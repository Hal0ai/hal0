// Models → Image → Files: the ComfyUI file inventory, on the Models page's shared catalog shell.
//
// ComfyUI weight files are not model-registry rows, so this view lists what is on
// disk under the ComfyUI models tree (GET /api/comfyui/files), grouped by component
// kind, with the catalog models that load each file. A file nobody in the catalog
// owns (an operator's own checkpoint or LoRA) can be deleted from here, behind a
// confirm. A catalog-owned file is removed by removing its model, and a symlink is
// removed through its target — the server refuses both, and the detail pane says so
// instead of offering a button that can only fail.
//
// `viewSwitch` is the Models | Files switch ImageModelsTab draws in the list header.
import { useMemo, useState } from 'react'
import { fmtBytes } from '@/api/hooks/useModels'
import { useComfyuiFiles, useComfyuiFileDelete, useComfyuiModels } from '@/api/hooks/useComfyui'
import { pageSections } from '@/dash/models/catalog-pure'
import {
  CatalogList,
  CatalogRow,
  CatalogToolbar,
  DetailCard,
  DetailEmpty,
  DetailFacts,
  DetailHeader,
  DetailUsedBy,
  StatusChip,
  toggleIn,
} from '@/dash/models/catalog-shell.jsx'
import { DownloadsPane } from '@/dash/models/DownloadsPane.jsx'
import {
  FILE_OWNER_FILTERS,
  fileFolder,
  fileKey,
  fileName,
  fileState,
  filterFiles,
  sortFiles,
} from '@/dash/models/image-catalog-pure'
import { ConfirmDialog } from '@/dash/primitives.jsx'
import { groupFilesByKind, kindLabel } from './image-files-pure'

const note = (msg, kind) => window.__hal0Toast && window.__hal0Toast(msg, kind)
const SORT_FIELDS = [
  { id: 'name', label: 'name' },
  { id: 'size', label: 'size' },
]

// What deletes a file a catalog model loads: removing the model. The CLI removes it in any
// state; the Models view here offers Remove only once the model is fully installed (a
// partly installed one shows Install there). The server keeps a file two models load only while
// another of them is complete (a partly installed one holds nothing back); only the CLI's
// `--include-shared` deletes it regardless, so the note names that command for it.
const ownedNote = (ids) =>
  ids.length === 1
    ? `Remove the model to delete this file: run hal0 comfyui models remove ${ids[0]} (the Models view offers Remove once the model is fully installed).`
    : `${ids.join(', ')} load this file. Removing one of them keeps it while another of them is fully installed. To delete it anyway: hal0 comfyui models remove ${ids[0]} --include-shared (the others stop working).`

export function ImageFilesTab({ viewSwitch = null }) {
  const filesQuery = useComfyuiFiles()
  const modelsQuery = useComfyuiModels()
  const del = useComfyuiFileDelete()
  const [selKey, setSelKey] = useState(null)
  const [confirmFile, setConfirmFile] = useState(null)
  const [text, setText] = useState('')
  const [owners, setOwners] = useState([])
  const [sortField, setSortField] = useState('name')
  const [sortDir, setSortDir] = useState('asc')

  const files = filesQuery.data?.files
  const root = filesQuery.data?.root
  const labelOf = useMemo(() => {
    const byId = new Map((modelsQuery.data?.models ?? []).map((m) => [m.id, m.label]))
    return (id) => byId.get(id) || id
  }, [modelsQuery.data])

  const every = Array.isArray(files) ? files : []
  const shown = sortFiles(filterFiles(every, { q: text, owners }), sortField, sortDir)
  const groups = groupFilesByKind(shown)
  const paged = pageSections(
    groups.map((g) => ({ key: g.kind, label: g.label, rows: g.files })),
    1,
    'all',
  )
  // The detail pane never opens empty: with nothing picked (or the picked file
  // gone after a delete) it shows the first row on screen.
  const all = groups.flatMap((g) => g.files)
  const selected = all.find((f) => fileKey(f) === selKey) ?? all[0] ?? null
  const usedBy = (f) => f.models.map(labelOf).join(', ')
  const totalBytes = every.reduce((n, f) => n + (f.size_bytes || 0), 0)
  const filtered = !!text.trim() || owners.length > 0

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

  return (
    <div className="models-layout" style={{ marginTop: 18 }} data-testid="imgf-view">
      <CatalogList
        toolbar={
          <CatalogToolbar
            q={text}
            onQ={setText}
            placeholder="search file, folder…"
            groups={[
              { id: 'owner', label: 'owner', chips: FILE_OWNER_FILTERS, selected: owners, onToggle: (id) => setOwners((s) => toggleIn(s, id)) },
            ]}
            sort={{
              fields: SORT_FIELDS,
              field: sortField,
              dir: sortDir,
              onField: setSortField,
              onDir: () => setSortDir((d) => (d === 'asc' ? 'desc' : 'asc')),
            }}
            onClear={() => {
              setText('')
              setOwners([])
            }}
          />
        }
        title="Image / ComfyUI"
        headerExtra={viewSwitch}
        right={`${every.length} file${every.length === 1 ? '' : 's'} · ${fmtBytes(totalBytes)} on disk`}
        paged={paged}
        sectionTestid={(kind) => `imgf-group-${kind}`}
        loading={filesQuery.isPending ? 'Loading files…' : null}
        error={filesQuery.isError ? filesQuery.error?.message || 'Failed to load files' : null}
        empty={
          filtered ? (
            'No files match — adjust the search or filters.'
          ) : (
            <>
              <div>No image model files on disk yet.</div>
              <div data-testid="imgf-empty-hint" style={{ marginTop: 6 }}>
                Install one from the Models view, or with <code>hal0 comfyui models install &lt;id&gt;</code> (
                <code>hal0 comfyui models list</code> shows the ids).
              </div>
            </>
          )
        }
        renderRow={(f) => {
          const key = fileKey(f)
          return (
            <CatalogRow
              key={key}
              testid={`imgf-row-${key}`}
              state={fileState(f, labelOf)}
              selected={!!selected && fileKey(selected) === key}
              onSelect={() => setSelKey(key)}
              name={fileName(f.relpath)}
              sub={fileFolder(f)}
              size={fmtBytes(f.size_bytes)}
              tags={
                <>
                  {f.symlink && (
                    <span className="chip" title="This file is a symlink">
                      link
                    </span>
                  )}
                  {f.models.length > 0 ? (
                    <span className="chip ok" title={`Loaded by ${usedBy(f)}`}>
                      Used by {usedBy(f)}
                    </span>
                  ) : (
                    <span className="chip warn" title="No catalog model loads this file">
                      Unmanaged
                    </span>
                  )}
                </>
              }
            />
          )
        }}
      />

      <div className="models-sidebar">
        {selected ? (
          <DetailCard testid="imgf-detail">
            <DetailHeader
              state={fileState(selected, labelOf)}
              name={fileName(selected.relpath)}
              source={fileFolder(selected)}
              chips={<StatusChip state={fileState(selected, labelOf)} />}
            />
            <DetailFacts
              facts={[
                { k: 'kind', v: kindLabel(selected.kind) },
                { k: 'size', v: fmtBytes(selected.size_bytes) },
                ...(selected.arch ? [{ k: 'family', v: selected.arch }] : []),
                ...(selected.api_id ? [{ k: 'API model id', v: selected.api_id, wrap: true }] : []),
                { k: 'path', v: root ? `${root.replace(/\/+$/, '')}/${fileKey(selected)}` : fileKey(selected), wrap: true, wide: true },
              ]}
            />
            <DetailUsedBy
              users={selected.models.map((id) => ({ name: labelOf(id), detail: id }))}
              none="Unmanaged — no catalog model loads this file."
            />
            <div className="mdl-detail-actions">
              {selected.models.length > 0 ? (
                <div className="mdl-detail-why" data-testid="imgf-owned-note">
                  {ownedNote(selected.models)}
                </div>
              ) : selected.symlink ? (
                <div className="mdl-detail-why" data-testid="imgf-symlink-note">
                  This file is a link. hal0 does not delete through a link: delete its target instead.
                </div>
              ) : (
                <button
                  type="button"
                  className="btn sm danger"
                  data-testid="imgf-delete"
                  disabled={del.isPending}
                  onClick={() => setConfirmFile(selected)}
                >
                  Delete…
                </button>
              )}
            </div>
          </DetailCard>
        ) : (
          <DetailEmpty>Select a file to see its details.</DetailEmpty>
        )}
        <DownloadsPane />
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
