// hal0 dashboard — Connections view (issue #549).
//
// Surface for the providers / upstreams registry that already lives at
// /api/providers + /api/upstreams. Three sections, all read-only
// (writes are TOML-only in v0.4):
//
//   • Providers  — remote upstreams (kind != "slot"), grouped by auth
//     posture. Source: GET /api/providers.
//   • Upstreams  — every routing target (slot fronts + remote). Source:
//     GET /api/upstreams. The "effective mapping" column renders
//     `<name> → <slot_name|kind:url>` so the operator can see which
//     virtual name fronts which slot at a glance — the "useful + unique"
//     read-out the issue calls out.
//   • Test button — per row, calls useTestUpstream() (POST
//     /api/upstreams/{name}/test) and shows pass/fail + latency/error
//     inline. Per-row state keeps previous results visible while a
//     new probe is in flight, so the operator can sweep through a
//     stack and still see the last-known good.

import { useProviders, useUpstreams, useTestUpstream } from '@/api/hooks/useConnections'

const { useState: useStateC, useMemo: useMemoC } = React;

function ConnectionsView() {
  const providersQuery = useProviders();
  const upstreamsQuery = useUpstreams();
  const providers = providersQuery.data ?? [];
  const upstreams = upstreamsQuery.data ?? [];

  // Per-row test state. Keyed by upstream name so the result survives
  // re-renders triggered by the list query's own poll. Storing a
  // {state:'idle'|'pending'|'ok'|'err', latency, error, status} object
  // means the previous pass/fail stays visible behind a new probe's
  // spinner — important when the operator sweeps through a stack of
  // 6-8 upstreams and wants to compare the roll.
  const [results, setResults] = useStateC({});
  const [pendingName, setPendingName] = useStateC(null);
  const testMut = useTestUpstream();

  const onTest = async (name) => {
    setPendingName(name);
    setResults((r) => ({ ...r, [name]: { state: 'pending' } }));
    try {
      const res = await testMut.mutateAsync(name);
      setResults((r) => ({
        ...r,
        [name]: {
          state: res.ok ? 'ok' : 'err',
          latency: res.latency_ms,
          models: res.models_count,
          status: res.status,
          error: res.error,
        },
      }));
    } catch (e) {
      setResults((r) => ({
        ...r,
        [name]: {
          state: 'err',
          error: e?.message || 'test failed',
          status: e?.status,
        },
      }));
    } finally {
      setPendingName(null);
    }
  };

  // Group providers by auth posture so the operator can spot unkeyed
  // remotes at a glance. No enforced order — sort by name within each.
  const providersByAuth = useMemoC(() => {
    const groups = { configured: [], unconfigured: [] };
    for (const p of providers) {
      (p.auth_configured ? groups.configured : groups.unconfigured).push(p);
    }
    groups.configured.sort((a, b) => a.name.localeCompare(b.name));
    groups.unconfigured.sort((a, b) => a.name.localeCompare(b.name));
    return groups;
  }, [providers]);

  const slotCount = upstreams.filter((u) => u.kind === 'slot').length;
  const remoteCount = upstreams.filter((u) => u.kind !== 'slot').length;

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Network</span>
        <h1>Connections</h1>
        <span className="vh-spacer" />
        <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
          {remoteCount} provider{remoteCount === 1 ? '' : 's'} · {slotCount} slot upstream{slotCount === 1 ? '' : 's'}
        </span>
      </div>

      <p className="mono" style={{fontSize: 11, color: "var(--fg-4)", margin: "0 0 18px", maxWidth: 720, lineHeight: 1.55}}>
        Effective routing targets. <b>Slot upstreams</b> front a lifecycle-managed slot;{' '}
        <b>remote providers</b> call out to a third-party API. Author by editing{' '}
        <code>/etc/hal0/upstreams.toml</code> + <code>hal0 config reload</code> — this view is read-only.
      </p>

      <ConnectionsSection
        title="Providers (remote)"
        rows={providers}
        grouped={providersByAuth}
        results={results}
        pendingName={pendingName}
        onTest={onTest}
        showGroupHeaders
        isLoading={providersQuery.isPending}
        isError={providersQuery.isError}
        errorMessage={providersQuery.error?.message}
        emptyMessage="No remote providers configured. Add one via /etc/hal0/upstreams.toml."
      />

      <ConnectionsSection
        title="All upstreams"
        rows={upstreams}
        results={results}
        pendingName={pendingName}
        onTest={onTest}
        isLoading={upstreamsQuery.isPending}
        isError={upstreamsQuery.isError}
        errorMessage={upstreamsQuery.error?.message}
        emptyMessage="No upstreams registered. The registry is normally primed on /api/health."
      />
    </div>
  );
}

// Section shell — card-style container with a header, optional group
// sub-headers, and the row grid. Renders loading/error/empty states.
function ConnectionsSection({
  title,
  rows,
  grouped,
  results,
  pendingName,
  onTest,
  showGroupHeaders = false,
  isLoading = false,
  isError = false,
  errorMessage = '',
  emptyMessage = 'Nothing here yet.',
}) {
  const renderRows = (rs) => rs.map((u) => (
    <ConnectionRow
      key={u.name}
      upstream={u}
      result={results[u.name]}
      pending={pendingName === u.name}
      onTest={() => onTest(u.name)}
    />
  ));

  return (
    <section className="card" style={{marginBottom: 18}}>
      <div className="card-h">
        <span className="card-h-eye mono">{title}</span>
        <span className="card-h-ct mono">{rows.length}</span>
      </div>
      <div className="cn-list-h mono">
        <span className="cn-c-name">Name</span>
        <span className="cn-c-kind">Kind</span>
        <span className="cn-c-target">Effective mapping</span>
        <span className="cn-c-auth">Auth</span>
        <span className="cn-c-models">Models</span>
        <span className="cn-c-test">Test</span>
      </div>
      {isLoading && (
        <div className="cn-empty mono">Loading…</div>
      )}
      {isError && (
        <div className="cn-empty err mono">Failed to load — {errorMessage || "unreachable"}</div>
      )}
      {!isLoading && !isError && rows.length === 0 && (
        <div className="cn-empty mono">{emptyMessage}</div>
      )}
      {!isLoading && !isError && showGroupHeaders && grouped && (
        <>
          {grouped.configured.length > 0 && (
            <>
              <div className="cn-section-label">auth configured · {grouped.configured.length}</div>
              {renderRows(grouped.configured)}
            </>
          )}
          {grouped.unconfigured.length > 0 && (
            <>
              <div className="cn-section-label cn-section-warn">auth missing · {grouped.unconfigured.length}</div>
              {renderRows(grouped.unconfigured)}
            </>
          )}
        </>
      )}
      {!isLoading && !isError && !showGroupHeaders && rows.length > 0 && renderRows(rows)}
    </section>
  );
}

// A single upstream row. The "effective mapping" cell is the unique
// read-out: <name> → <slot_name> for slot fronts, <name> → <url> for
// remotes, and we tag remote rows with the auth style so the operator
// can tell bearer from x-api-key at a glance.
function ConnectionRow({ upstream, result, pending, onTest }) {
  const u = upstream;
  const target = u.kind === 'slot'
    ? (u.slot_name ? `→ slot ${u.slot_name}` : '→ (unbound slot)')
    : `→ ${u.url || '(no url)'}`;
  const modelCount = Array.isArray(u.models) ? u.models.length : 0;
  const declaredCount = Array.isArray(u.advertise_models) ? u.advertise_models.length : 0;
  // Show the cached model count when the registry has probed the
  // upstream at least once; otherwise fall back to the declared list.
  const mcount = modelCount > 0 ? modelCount : declaredCount;
  const mlabel = mcount > 0 ? `${mcount}` : '—';
  const authClass = u.auth_configured ? 'chip ok' : 'chip warn';
  const authLabel = u.auth_configured
    ? (u.auth_style || 'configured')
    : (u.auth_value_env ? `needs ${u.auth_value_env}` : 'none');

  return (
    <div className="cn-row">
      <span className="cn-c-name">
        <span className={"cn-dot " + (u.kind === 'slot' ? 'ready' : 'ok')} />
        <span className="mono">{u.name}</span>
      </span>
      <span className="cn-c-kind">
        <span className="chip">{u.kind}</span>
      </span>
      <span className="cn-c-target mono">
        <span className="cn-target">{target}</span>
        {u.warmup_strategy && (
          <span className="cn-sub mono">warmup: {u.warmup_strategy}</span>
        )}
      </span>
      <span className="cn-c-auth">
        <span className={authClass} title={u.auth_value_env || ''}>
          {authLabel}
        </span>
      </span>
      <span className="cn-c-models mono">
        <b>{mlabel}</b>
        {mcount > 0 && modelCount > 0 && <span className="cn-sub mono">cached</span>}
        {mcount > 0 && modelCount === 0 && <span className="cn-sub mono">declared</span>}
      </span>
      <span className="cn-c-test">
        <TestCell result={result} pending={pending} onTest={onTest} name={u.name} />
      </span>
    </div>
  );
}

// Test cell — button + inline result. States:
//   idle     — button only
//   pending  — spinner + "testing…", button disabled
//   ok       — green latency + small status badge, "test again" button
//   err      — red error + status, "retry" button
function TestCell({ result, pending, onTest, name }) {
  if (pending) {
    return (
      <>
        <span className="cn-test-pending mono">testing…</span>
        <button className="btn ghost sm" disabled>{Icons.warn} Test</button>
      </>
    );
  }
  if (result?.state === 'ok') {
    return (
      <>
        <span className="cn-test-ok mono">
          <span className="cn-dot ok" />{result.latency != null ? `${result.latency} ms` : 'ok'}
          {result.models != null && <span className="cn-sub mono"> · {result.models} model{result.models === 1 ? '' : 's'}</span>}
        </span>
        <button className="btn ghost sm" onClick={onTest} aria-label={`Re-test ${name}`}>↻</button>
      </>
    );
  }
  if (result?.state === 'err') {
    return (
      <>
        <span className="cn-test-err mono" title={result.error || ''}>
          <span className="cn-dot warn" />{result.error ? truncate(result.error, 28) : 'failed'}
          {result.status != null && <span className="cn-sub mono"> · {result.status}</span>}
        </span>
        <button className="btn ghost sm" onClick={onTest} aria-label={`Retry ${name}`}>↻</button>
      </>
    );
  }
  return (
    <button className="btn ghost sm" onClick={onTest} aria-label={`Test ${name}`}>
      {Icons.warn} Test
    </button>
  );
}

function truncate(s, n) {
  if (!s) return ''
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

Object.assign(window, { ConnectionsView });
