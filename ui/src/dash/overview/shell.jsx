// hal0 Overview v2 — shared card shell: OvCard, OvPill, OvSpark.
//
// Window-global module (same pattern as cards-shell.jsx): every overview
// section builds on these three and the classes in overview.css.

// ─── OvCard ──────────────────────────────────────────────────────────────────
// section.ov-card > header.ov-card-h(.ov-card-t, .ov-card-n, .sp, right)
//                 + div.ov-card-b[.flush] + footer.ov-card-f
function OvCard({ title, note, right, foot, flush, className, children }) {
  return (
    <section className={'ov-card' + (className ? ' ' + className : '')}>
      <header className="ov-card-h">
        <span className="ov-card-t">{title}</span>
        {note && <span className="ov-card-n">{note}</span>}
        <span className="sp" />
        {right}
      </header>
      <div className={'ov-card-b' + (flush ? ' flush' : '')}>{children}</div>
      {foot && <footer className="ov-card-f">{foot}</footer>}
    </section>
  )
}

// ─── OvPill ──────────────────────────────────────────────────────────────────
// tone: 'ok' | 'warn' | 'err' | 'img' | 'accent' | 'dim'
function OvPill({ tone, children }) {
  return <span className={'ov-pill' + (tone ? ' ' + tone : '')}>{children}</span>
}

// ─── OvSpark ─────────────────────────────────────────────────────────────────
// Area + line + endpoint dot. Needs two points to draw a line; fewer renders
// nothing (fail-soft: no fabricated trend).
function OvSpark({ values, width = 84, height = 22, color = 'var(--accent)' }) {
  if (!Array.isArray(values) || values.length < 2) return null
  const pad = 2
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const step = (width - pad * 2) / (values.length - 1)
  const pts = values.map((v, i) => [pad + i * step, height - pad - ((v - min) / span) * (height - pad * 2)])
  const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')
  const [lx, ly] = pts[pts.length - 1]
  const area = `${line} L${lx.toFixed(1)} ${height} L${pts[0][0].toFixed(1)} ${height} Z`
  return (
    <svg className="ov-spk" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path d={area} fill={color} opacity="0.14" />
      <path d={line} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r="2" fill={color} />
    </svg>
  )
}

Object.assign(window, { OvCard, OvPill, OvSpark })
