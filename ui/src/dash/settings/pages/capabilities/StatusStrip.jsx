// Jump-nav status strip: one chip per capability, scroll-links to its panel.
// Reads the same useCapabilities() query the panels use (react-query dedupes).
import { useCapabilities } from '@/api/hooks/useCapabilities'

const CHIPS = [
  { id: "cap-tts",    label: "TTS",    group: "voice", child: "tts" },
  { id: "cap-stt",    label: "STT",    group: "voice", child: "stt" },
  { id: "cap-embed",  label: "Embed",  group: "embed", child: "embed" },
  { id: "cap-rerank", label: "Rerank", group: "embed", child: "rerank" },
  { id: "cap-img",    label: "Image",  group: "img",   child: "img" },
]

function dotColor(sel) {
  const st = sel?.status || "offline"
  if (st === "ready" || st === "serving") return "var(--ok)"
  if (st === "starting" || st === "warming") return "var(--warn)"
  return "var(--fg-4)"
}

export function StatusStrip() {
  const capsQuery = useCapabilities()
  const selections = capsQuery.data?.selections || {}
  const jump = (id) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" })
  return (
    <div className="s-panel" style={{display: "flex", flexWrap: "wrap", gap: 8, padding: "8px 12px", marginBottom: 12}}>
      {CHIPS.map(c => {
        const sel = selections[c.group]?.[c.child]
        return (
          <button key={c.id} className="chip mono" onClick={() => jump(c.id)}
            title={sel ? `${sel.status || "offline"}${sel.slot ? ` · slot ${sel.slot}` : ""}` : "status unknown"}
            style={{fontSize: 11, padding: "2px 10px", cursor: "pointer", background: "transparent", display: "inline-flex", alignItems: "center", gap: 6}}>
            <span style={{width: 7, height: 7, borderRadius: "50%", background: dotColor(sel), display: "inline-block"}} />
            {c.label}
          </button>
        )
      })}
      <button className="chip mono" onClick={() => jump("cap-npu")}
        style={{fontSize: 11, padding: "2px 10px", cursor: "pointer", background: "transparent", color: "var(--fg-3)"}}>
        NPU ▾
      </button>
    </div>
  )
}
