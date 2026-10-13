// GENERAL ▸ Network (#2382) — the browser origins hal0 accepts.
//
// Its own page rather than a panel on Security: the Security page is a
// status-only surface that must never render an input (security-page-v3
// e2e: "never renders a key value — status only, no inputs on the page").
import { NetworkPanel } from './NetworkPanel.jsx'

export function NetworkPage() {
  return (
    <div className="s-section" data-testid="network-page">
      <h2>Network</h2>
      <p className="desc">
        Browser origins hal0 accepts for state-changing requests and WebSocket upgrades, on top of
        the installer&apos;s list. Set the public URL when hal0 sits behind a reverse proxy. Same
        as <span className="mono">hal0 network public-url</span> and{' '}
        <span className="mono">hal0 network origins</span>.
      </p>
      <NetworkPanel />
    </div>
  )
}
