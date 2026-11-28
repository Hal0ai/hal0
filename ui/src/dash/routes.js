// The dashboard's top-level pages: the first segment of a `#page/...` address.
// `parseRoute` (main.jsx) sends any other head to the Overview, so a link whose head is not here
// silently lands there. `routes.test.ts` scans the dashboard's links against this list (#4107).
export const ROUTES = ["dashboard", "slots", "models", "logs", "agent", "memory", "settings", "mcp", "board", "services", "benchmarks"];

/** Whether `address` (with or without the leading `#`) opens a real page. */
export function isKnownRoute(address) {
  const path = String(address || "").replace(/^#/, "").split("?")[0];
  const [head, next] = path.split("/");
  // The one alias parseRoute understands: #agents/mcp is the MCP page.
  if (head === "agents") return next === "mcp";
  return ROUTES.includes(head);
}
