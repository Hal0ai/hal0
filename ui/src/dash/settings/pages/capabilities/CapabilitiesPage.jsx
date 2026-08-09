// MODELS & INFERENCE ▸ AI Capabilities — the unified settings surface for
// every non-chat capability slot: TTS, STT, embeddings, reranking, image
// generation, plus the collapsed NPU anchor. Absorbs the former Voice /
// Image Generation / NPU pages (their #settings/<id> deep links resolve here
// via SECTION_ALIASES). Chat models stay on Loaded Models; the memory recall
// reranker client stays on Memory (linked from the Reranking panel).
import { useCapabilities } from '@/api/hooks/useCapabilities'
import { useSettingsClient } from '../../data/settingsClient.js'
import { StatusStrip } from './StatusStrip.jsx'
import { TtsPanel } from './TtsPanel.jsx'
import { SttPanel } from './SttPanel.jsx'
import { EmbedPanel } from './EmbedPanel.jsx'
import { RerankPanel } from './RerankPanel.jsx'
import { ImagePanel } from './ImagePanel.jsx'
import { NpuAnchorPanel } from './NpuAnchorPanel.jsx'

export function CapabilitiesPage() {
  const capsQuery = useCapabilities()
  const { registry } = useSettingsClient()

  return (
    <div className="s-section">
      <h2>AI Capabilities</h2>
      <p className="desc">Speech, embeddings, reranking, and image generation. Chat models live in Loaded Models.</p>

      {capsQuery.isError && (
        <div className="err">{capsQuery.error?.message || "Could not load capabilities — Save is disabled until the probe succeeds"}</div>
      )}

      <StatusStrip />
      <div id="cap-tts" style={{marginBottom: 12}}><TtsPanel registry={registry} /></div>
      <div id="cap-stt" style={{marginBottom: 12}}><SttPanel /></div>
      <div id="cap-embed" style={{marginBottom: 12}}><EmbedPanel /></div>
      <div id="cap-rerank" style={{marginBottom: 12}}><RerankPanel /></div>
      <div id="cap-img" style={{marginBottom: 12}}><ImagePanel registry={registry} /></div>
      <div id="cap-npu"><NpuAnchorPanel registry={registry} /></div>
    </div>
  )
}
