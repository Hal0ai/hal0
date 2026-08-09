// Embeddings — embed.embed capability slot (llama-server --embedding profile,
// seed default qwen3-embedding-0-6b-q8-0). First settings surface for this
// selection: /api/capabilities always shipped catalogs/selections for it, but
// no page consumed them before the AI Capabilities unification.
// Note: Hindsight (memory) embeds server-side with its own bundled model —
// this slot serves /v1/embeddings API traffic (RAG, external clients) only.
import { SRow } from '../../shared/SRow.jsx'
import { useCapabilitySelection } from './useCapabilitySelection.js'
import { statusChip, PanelHeader, PanelFooter, EnabledRow, ModelRow } from './shared.jsx'

export function EmbedPanel() {
  const sel = useCapabilitySelection('embed', 'embed')

  const doSave = async () => {
    try {
      await sel.save()
      window.__hal0Toast && window.__hal0Toast("Embeddings settings saved", "ok")
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Embeddings save failed — ${e?.message || "see logs"}`, "err")
    }
  }

  return (
    <div className="s-panel">
      <PanelHeader title="Embeddings" info="embed.embed slot · llama-server --embedding" chip={statusChip(sel.status)} />
      <EnabledRow enabled={sel.enabled} setEnabled={sel.setEnabled} />
      <ModelRow items={sel.catalogItems} value={sel.model} onChange={sel.setModel}
        placeholder="model id (e.g. qwen3-embedding-0-6b-q8-0)"
        emptyHint="no installed embedding models — install one in the Models view" />
      <SRow k="Serves" sub="memory (Hindsight) embeds with its own bundled model — this slot serves API/RAG traffic" mono
        v={<span style={{color: "var(--fg-4)"}}>/v1/embeddings</span>} />
      <PanelFooter dirty={sel.dirty} onReset={sel.reset} onSave={doSave}
        disabled={!sel.dirty || sel.loading || sel.errored || sel.applyCapability.isPending}
        saving={sel.applyCapability.isPending} label="Save embeddings" />
    </div>
  )
}
