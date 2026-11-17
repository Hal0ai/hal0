// Models → Image: one tab, two views of the ComfyUI model store on the shared catalog shell.
//
//   Models  the image-model catalog (ImageCatalogView): what can be installed, what is, and
//           whether each fits this box.
//   Files   every weight file on disk (ImageFilesTab), including the ones an operator added by
//           hand that no catalog model owns.
//
// The switch sits in the list header of whichever view shows. `addSeq` is bumped by the page
// header's "Install image model": it brings up the Models view filtered to what is not installed.
import { useEffect, useState } from 'react'
import { ImageCatalogView } from './ImageCatalogView'
import { ImageFilesTab } from './ImageFilesTab'

const VIEWS = [
  { id: 'models', label: 'Models' },
  { id: 'files', label: 'Files' },
]

export function ImageModelsTab({ addSeq = 0 }) {
  const [view, setView] = useState('models')
  useEffect(() => {
    if (addSeq) setView('models')
  }, [addSeq])

  const viewSwitch = (
    <span className="mdl-seg" role="group" aria-label="View" data-testid="img-view-switch">
      {VIEWS.map((v) => (
        <button
          key={v.id}
          type="button"
          className={view === v.id ? 'on' : ''}
          aria-pressed={view === v.id}
          data-testid={`img-view-${v.id}`}
          onClick={() => setView(v.id)}
        >
          {v.label}
        </button>
      ))}
    </span>
  )
  return view === 'files' ? <ImageFilesTab viewSwitch={viewSwitch} /> : <ImageCatalogView viewSwitch={viewSwitch} addSeq={addSeq} />
}
