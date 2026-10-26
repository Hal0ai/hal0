// True while the browser tab is visible. The dock closes its two SSE streams when the tab is
// hidden: hal0 serves HTTP/1.1, so each open tab holding two streams eats the browser's
// six-connections-per-host cap.

import { useEffect, useState } from 'react'

const isVisible = () => typeof document === 'undefined' || document.visibilityState !== 'hidden'

export function useDocumentVisible() {
  const [visible, setVisible] = useState(isVisible)
  useEffect(() => {
    if (typeof document === 'undefined') return undefined
    const onChange = () => setVisible(isVisible())
    document.addEventListener('visibilitychange', onChange)
    onChange()
    return () => document.removeEventListener('visibilitychange', onChange)
  }, [])
  return visible
}
