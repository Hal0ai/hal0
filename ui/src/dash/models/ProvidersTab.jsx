// Models → Providers: the list. One section per provider, its models switched on and off in place.
//
// Props:
//   selected  { provider: string | null, model: string | null }   what the detail panel shows
//   onSelect  (next: { provider, model }) => void                  replace the selection
//   onAdd     () => void                                           open the Add provider modal
//
// Placeholder body: the real list replaces it without changing these props.
export function ProvidersTab({ selected, onSelect, onAdd }) {
  return (
    <div className="providers-tab" data-testid="providers-tab">
      Providers
    </div>
  )
}
