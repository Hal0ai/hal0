// INTEGRATIONS ▸ Secrets — provider / pull-auth / custom keys, stored
// encrypted at rest. Extracted verbatim from settings.jsx SecretsSection +
// HfTokenField (P3-ui split phase 1). The `id` stays "secrets" (unchanged)
// so #settings/secrets deep links keep working. Filed under INTEGRATIONS —
// closest fit among the target IA groups (secret keys mostly gate external
// provider integrations); spec's INTEGRATIONS▸API-Compat/Client-Setup
// content itself is still G[§21.5/§21.9] MISSING.
import { useState } from 'react'
import { useSecrets, useSecretSet, useSecretDelete } from '@/api/hooks/useSecrets'
import { SECRET_PRESETS, AddSecretModal } from '../../../extra-modals.jsx'
import { Icons } from '../../../chrome.jsx'
import { SRow } from '../../shared/SRow.jsx'

// Dedicated, discoverable HuggingFace-token field (P4). Wraps the existing
// /api/secrets store under the fixed name HF_TOKEN — set/delete update os.environ
// live (no restart), so the next gated/large pull authenticates immediately.
function HfTokenField() {
  const secretsQuery = useSecrets();
  const setSecret = useSecretSet();
  const delSecret = useSecretDelete();
  const [val, setVal] = useState("");
  const isSet = (secretsQuery.data ?? []).some(s => s.name === "HF_TOKEN" && s.set);
  return (
    <div className="s-panel" style={{padding: 16, marginBottom: 16}}>
      <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 6}}>HuggingFace token</div>
      <p className="desc" style={{margin: "0 0 10px"}}>
        Needed for gated / large model pulls. Stored encrypted; applied live (no restart).
        Status: {isSet ? <span style={{color: "var(--ok)"}}>set ✓</span> : <span style={{color: "var(--fg-4)"}}>not set</span>}
      </p>
      <div style={{display: "flex", gap: 8}}>
        <input
          type="password"
          className="input mono"
          aria-label="HuggingFace token"
          value={val}
          onChange={e => setVal(e.target.value)}
          placeholder={isSet ? "•••••••• (set) — enter to replace" : "hf_…"}
          style={{flex: 1, padding: "8px 10px", fontSize: 13}}
        />
        <button
          className="btn"
          disabled={!val.trim() || setSecret.isPending}
          onClick={() => setSecret.mutate({ name: "HF_TOKEN", value: val.trim() }, { onSuccess: () => setVal("") })}
        >
          {setSecret.isPending ? "Saving…" : "Save"}
        </button>
        {isSet && (
          <button className="btn ghost" disabled={delSecret.isPending} onClick={() => delSecret.mutate("HF_TOKEN")}>
            {delSecret.isPending ? "Clearing…" : "Clear"}
          </button>
        )}
      </div>
    </div>
  );
}

// Per-key descriptions shared with the Add-Secret modal. Anything not in
// the preset table is a user-defined key — say so instead of mislabelling
// it as a fallback provider.
const SECRET_DESCRIPTIONS = Object.fromEntries(SECRET_PRESETS.map(p => [p.id, p.desc]));
const secretDescription = (name) =>
  SECRET_DESCRIPTIONS[name] || "Custom key · exported to hal0 services and slot containers as an env var";

export function SecretsPage() {
  const [addOpen, setAddOpen] = useState(false);
  const [addTarget, setAddTarget] = useState(null);
  const secretsQuery = useSecrets();
  const delSecret = useSecretDelete();
  const rows = secretsQuery.data ?? [];
  const openAdd = (name) => { setAddTarget(name || null); setAddOpen(true); };
  return (
    <div className="s-section">
      <h2>Secrets</h2>
      <p className="desc">Stored encrypted at rest, never shown again after saving. Each key is exported to hal0 services and slot containers as an environment variable — model-pull auth, fallback providers, or your own custom keys.</p>
      <HfTokenField />
      {secretsQuery.isLoading && (
        <div style={{padding: 16, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Loading…</div>
      )}
      {secretsQuery.isError && (
        <div className="err">{secretsQuery.error?.message || "Could not load secrets"}</div>
      )}
      <div className="s-panel">
        {rows.length === 0 && !secretsQuery.isLoading && !secretsQuery.isError && (
          <div className="s-row" style={{padding: "18px 16px"}}>
            <span className="mono" style={{fontSize: 12, color: "var(--fg-4)"}}>no secrets configured · add one</span>
          </div>
        )}
        {rows.map(s => (
          <SRow
            key={s.name}
            k={s.name}
            sub={secretDescription(s.name)}
            mono
            v={s.set
              ? <span style={{color: "var(--ok)"}}>{s.masked || '••• · set'}</span>
              : <span style={{color: "var(--fg-4)"}}>not set</span>}
            actions={s.set
              ? (<>
                  <button className="btn ghost sm" onClick={() => openAdd(s.name)}>Update</button>
                  <button
                    className="btn danger sm"
                    disabled={delSecret.isPending && delSecret.variables === s.name}
                    onClick={() => {
                      delSecret.mutate(s.name, {
                        onSuccess: () => window.__hal0Toast && window.__hal0Toast(`${s.name} removed`, "warn"),
                        onError: (err) => window.__hal0Toast && window.__hal0Toast(
                          `Remove failed — ${err?.message || "see logs"}`,
                          "err",
                        ),
                      });
                    }}
                  >{delSecret.isPending && delSecret.variables === s.name ? "Removing…" : "Remove"}</button>
                </>)
              : <button className="btn ghost sm" onClick={() => openAdd(s.name)}>Add</button>}
          />
        ))}
      </div>
      <div style={{marginTop: 14, display: "flex", justifyContent: "space-between", alignItems: "center"}}>
        <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
          {rows.length > 0 ? `${rows.length} key${rows.length === 1 ? "" : "s"} stored` : "add keys for model pulls, fallback providers, or custom env vars"}
        </span>
        <button className="btn" onClick={() => openAdd(null)}>{Icons.plus} Add secret</button>
      </div>
      <AddSecretModal open={addOpen} initialName={addTarget} onClose={() => setAddOpen(false)} />
    </div>
  );
}
