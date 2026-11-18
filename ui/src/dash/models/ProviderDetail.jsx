// Models → Providers: the right panel. A provider's facts and actions, or one model's facts and edit.
//
// Props:
//   selected  { provider: string | null, model: string | null }   what to show
//   onSelect  (next: { provider, model }) => void                  replace the selection
//
// Placeholder body: the real panel replaces it without changing these props.
export function ProviderDetail({ selected, onSelect }) {
  return (
    <div className="provider-detail" data-testid="provider-detail">
      {selected?.provider ?? 'No provider selected'}
    </div>
  )
}
