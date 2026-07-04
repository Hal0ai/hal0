// Local-only override: use the container's pre-installed chromium
// (playwright 1.60 expects build 1223; the sandbox ships 1194).
import base from './playwright.config'

export default {
  ...base,
  use: {
    ...(base as any).use,
    launchOptions: { executablePath: '/opt/pw-browsers/chromium' },
  },
}
