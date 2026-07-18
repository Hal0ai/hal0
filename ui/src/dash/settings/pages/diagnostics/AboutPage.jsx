// DIAGNOSTICS ▸ About — version + links.
// Extracted verbatim from settings.jsx AboutSection (P3-ui split phase 1).
// The `id` stays "about" (unchanged) so #settings/about deep links keep
// working.
import { useUpdateState } from '@/api/hooks/useUpdates'
import { Icons } from '../../../chrome.jsx'
import { SRow } from '../../shared/SRow.jsx'

export function AboutPage() {
  // #543: read hal0 version live from /api/updates/state instead of a
  // hardcoded literal that drifts from the running build. Empty until the
  // first response lands so the layout doesn't shift around a stale value.
  const stateQuery = useUpdateState();
  const liveVersion = stateQuery.data?.hal0?.current || "";
  return (
    <div className="s-section">
      <h2>About</h2>
      <div className="s-panel">
        <SRow k="hal0" mono v={liveVersion ? `${liveVersion} — container slots` : "—"} />
        <SRow k="License" v="Apache-2.0" />
        <SRow k="Repository" mono v="github.com/Hal0ai/hal0" actions={<a className="btn ghost sm" href="https://github.com/Hal0ai/hal0" target="_blank" rel="noreferrer">{Icons.ext} Open</a>} />
        <SRow k="Docs" v="hal0.dev/docs" actions={<a className="btn ghost sm" href="https://hal0.dev/docs/" target="_blank" rel="noreferrer">{Icons.ext} Open</a>} />
        <SRow k="Discord" v="discord.gg/hal0" actions={<a className="btn ghost sm" href="https://discord.gg/hal0" target="_blank" rel="noreferrer">{Icons.ext} Join</a>} />
      </div>
      <div style={{marginTop: 14, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)"}}>
        Built on FLM (XDNA2), llama.cpp, whisper.cpp, sd.cpp, Kokoro, Cognee.
      </div>
    </div>
  );
}
