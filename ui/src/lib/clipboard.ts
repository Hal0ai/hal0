// Copy-to-clipboard with a REAL outcome (#2214).
//
// navigator.clipboard.writeText returns a Promise; the old house idiom
// try/caught around the call, which only catches a synchronous throw. A real
// browser failure — permission denied, or a non-secure LAN origin where
// navigator.clipboard is absent entirely — surfaced as an async rejection (or
// a sync TypeError), so the success toast fired anyway and the rejection went
// unhandled. This helper awaits the write and answers what actually happened;
// callers toast off the boolean.
//
// On failure (or where the async API is missing — hal0 dashboards are often
// served over plain http on a LAN) it falls back to the legacy hidden-textarea
// execCommand("copy") path that connections.jsx already carried.
export async function copyTextToClipboard(text: string): Promise<boolean> {
  const s = String(text);
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(s);
      return true;
    }
  } catch {
    // fall through to the legacy path
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = s;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok === true;
  } catch {
    return false;
  }
}
