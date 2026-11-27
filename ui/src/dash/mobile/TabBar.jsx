// hal0 dashboard — phone bottom tab bar (≤720px; tabbar.css hides it everywhere else).
// Four destinations navigate, the fifth opens the NavDrawer, which keeps the full nav. Icons,
// labels and the active-section rule are the sidebar's own (useNavItems, _navActive), so the
// bar and the sidebar cannot drift: Overview, Slots, Models and Agents highlight across their
// whole section, and #mcp counts as Agents.
import { Icons, useNavItems, _navActive } from '../chrome.jsx'

const TAB_IDS = ['dashboard', 'slots', 'models', 'agent']

/**
 * @param {{ route: string, param?: string, onMore: () => void, moreOpen?: boolean }} props
 */
export function TabBar({ route, param, onMore, moreOpen = false }) {
  const items = useNavItems()
  const tabs = TAB_IDS.map((id) => items.find((it) => it.id === id)).filter(Boolean)
  const inTabs = tabs.some((it) => _navActive(route, param, it.id))
  return (
    <nav className="tabbar" aria-label="Primary" data-testid="tabbar">
      {tabs.map((it) => {
        const active = _navActive(route, param, it.id)
        return (
          <a
            key={it.id}
            href={'#' + it.id}
            className={'tabbar-tab' + (active ? ' active' : '')}
            aria-current={active ? 'page' : undefined}
            data-testid={'tabbar-' + it.id}
          >
            <span className="tabbar-ico">{it.icon}</span>
            <span className="tabbar-lbl">{it.label}</span>
          </a>
        )
      })}
      {/* More is not a destination, so it never carries aria-current; it is lit while the
          drawer is open, or while the page is one only the drawer reaches (Memory, Logs...). */}
      <button
        type="button"
        className={'tabbar-tab tabbar-more' + (moreOpen || !inTabs ? ' active' : '')}
        aria-haspopup="menu"
        aria-expanded={moreOpen}
        data-testid="tabbar-more"
        onClick={onMore}
      >
        <span className="tabbar-ico">{Icons.menu}</span>
        <span className="tabbar-lbl">More</span>
      </button>
    </nav>
  )
}
