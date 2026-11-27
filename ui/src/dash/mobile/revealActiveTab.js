// A scrolling tab strip (`.slot-tabs`) that loads on a tab past its right edge would show the
// wrong thing: the open tab is off-screen. Used as a callback ref (stable identity, so it runs
// on mount only), this scrolls the strip, never the page, so the open tab sits in the middle.
//
// The strip's layout is not final on the first frame (web fonts, the tab counts), so the scroll
// is repeated while it settles, and stops the moment the user touches the strip.
export function revealActiveTab(strip) {
  if (!strip) return
  let touched = false
  const stop = () => { touched = true }
  strip.addEventListener('pointerdown', stop, { once: true })
  strip.addEventListener('wheel', stop, { once: true, passive: true })

  const reveal = () => {
    if (touched || !strip.isConnected || strip.scrollWidth <= strip.clientWidth) return
    const tab = strip.querySelector('[aria-selected="true"], [aria-current="page"], .on')
    if (!tab) return
    const left = tab.getBoundingClientRect().left - strip.getBoundingClientRect().left + strip.scrollLeft
    const target = left - (strip.clientWidth - tab.offsetWidth) / 2
    strip.scrollLeft = Math.max(0, target)
  }
  requestAnimationFrame(reveal)
  setTimeout(reveal, 150)
  setTimeout(reveal, 500)
  if (typeof document !== 'undefined' && document.fonts?.ready) document.fonts.ready.then(reveal)
}
