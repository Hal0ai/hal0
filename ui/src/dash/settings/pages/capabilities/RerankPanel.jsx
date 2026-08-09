// Reranking — embed.rerank capability slot (llama-server --reranking profile,
// seed default bge-reranker-v2-m3-q4_k_m, canonical slot name `rerank`).
// First settings surface for this selection (see EmbedPanel note).
// The memory recall reranker CLIENT ([memory.embedding].rerank_*) is a
// separate concern and stays on the Memory page — linked below.
import { SRow } from '../../shared/SRow.jsx'
import { useCapabilitySelection } from './useCapabilitySelection.js'
import { statusChip, PanelHeader, PanelFooter, EnabledRow, ModelRow } from './shared.jsx'

export function RerankPanel() {
  const sel = useCapabilitySelection('embed', 'rerank')

  const doSave = async () => {
    try {
      await sel.save()
      window.__hal0Toast && window.__hal0Toast("Reranking settings saved", "ok")
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Reranking save failed — ${e?.message || "see logs"}`, "err")
    }
  }

  return (
    <div className="s-panel">
      <PanelHeader title="Reranking" info="embed.rerank selection · rerank slot · llama-server --reranking" chip={statusChip(sel.status)} />
      <EnabledRow enabled={sel.enabled} setEnabled={sel.setEnabled} />
      <ModelRow items={sel.catalogItems} value={sel.model} onChange={sel.setModel}
        placeholder="model id (e.g. bge-reranker-v2-m3)"
        emptyHint="no installed reranking models — install one in the Models view" />
      <SRow k="Serves" sub="public route /v1/rerankings is rewritten to llama-server's native /v1/rerank" mono
        v={<span style={{color: "var(--fg-4)"}}>/v1/rerank</span>} />
      <SRow k="Memory recall reranker" sub="the memory subsystem's second-pass reranker client is configured separately" v={
        <a href="#settings/memory" className="mono" style={{fontSize: 11, color: "var(--accent)"}}>Memory settings →</a>
      } />
      <PanelFooter dirty={sel.dirty} onReset={sel.reset} onSave={doSave}
        disabled={!sel.dirty || sel.loading || sel.errored || sel.applyCapability.isPending}
        saving={sel.applyCapability.isPending} label="Save reranking" />
    </div>
  )
}
