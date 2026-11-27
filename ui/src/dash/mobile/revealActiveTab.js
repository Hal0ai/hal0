// A scrolling tab strip (`.slot-tabs`) that loads on a tab past its right edge would show the
// wrong thing: the open tab is off-screen. Used as a callback ref (stable identity, so it runs
// on mount only), this scrolls the strip, never the page, so the open tab sits in the middle.
//
// The strip's layout is not final on the first frame: web fonts load, tab counts fill in, and a
// tab can be added once its data arrives (the Slots NPU tab). So the scroll is repeated whenever
// the strip's size or contents change, for the first SETTLE_MS, and stops for good the moment
// the user touches the strip. Timers alone were not enough: on a slow machine the strip was
// still changing after the last one fired.
const SETTLE_MS = 10_000

export function revealActiveTab(strip) {
  if (!strip) return
  let done = false
  const observers = []
  const stop = () => {
    done = true
    for (const o of observers) o.disconnect()
  }
  strip.addEventListener('pointerdown', stop, { once: true })
  strip.addEventListener('wheel', stop, { once: true, passive: true })
  strip.addEventListener('keydown', stop, { once: true })

  const reveal = () => {
    if (done) return
    if (!strip.isConnected) return stop()
    if (strip.scrollWidth <= strip.clientWidth) return
    const tab = strip.querySelector('[aria-selected="true"], [aria-current="page"], .on')
    if (!tab) return
    const left = tab.getBoundingClientRect().left - strip.getBoundingClientRect().left + strip.scrollLeft
    const target = Math.max(0, left - (strip.clientWidth - tab.offsetWidth) / 2)
    if (Math.abs(strip.scrollLeft - target) > 1) strip.scrollLeft = target
  }

  requestAnimationFrame(reveal)
  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(reveal)
    ro.observe(strip)
    for (const child of strip.children) ro.observe(child)
    observers.push(ro)
  }
  if (typeof MutationObserver !== 'undefined') {
    const mo = new MutationObserver((records) => {
      // a tab that arrives late changes the strip's width without resizing the strip itself
      const ro = observers[0]
      if (ro && typeof ro.observe === 'function') {
        for (const r of records) for (const n of r.addedNodes) if (n.nodeType === 1 && n.parentNode === strip) ro.observe(n)
      }
      reveal()
    })
    mo.observe(strip, { childList: true, subtree: true, characterData: true })
    observers.push(mo)
  }
  if (typeof document !== 'undefined' && document.fonts?.ready) document.fonts.ready.then(reveal)
  setTimeout(stop, SETTLE_MS)
}
