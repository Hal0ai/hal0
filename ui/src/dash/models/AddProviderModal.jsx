// Models → Providers: the Add provider modal (one page that grows: tiles, connection, model checklist).
//
// Props:
//   open     boolean
//   onClose  () => void
//   onAdded  (name: string) => void   called with the new provider's name once it exists
//
// Placeholder body: the real flow replaces it without changing these props.
export function AddProviderModal({ open, onClose, onAdded }) {
  if (!open) return null
  return (
    <div className="add-provider-modal" data-testid="add-provider-modal" role="dialog" aria-label="Add provider">
      <button type="button" className="btn ghost" onClick={onClose}>Close</button>
    </div>
  )
}
