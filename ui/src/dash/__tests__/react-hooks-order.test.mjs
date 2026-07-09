// Regression: React hook calls must never appear inside an Array.map
// callback body — that is the canonical pattern that produces the
// production minified error "Rendered more hooks than during the
// previous render" (invariant 310) the moment the map's source array
// changes length between renders.
//
// This static test parses every JSX/JS file under src/dash with
// @babel/parser (already on disk via Vite/Babel) and reports any
// call expression to a React hook function that lives inside an
// Array.prototype.map / .forEach / etc. callback.
// Pure node — no Jest, no React runtime.
//
// Run: node ui/src/dash/__tests__/react-hooks-order.test.mjs

import { parse } from "@babel/parser";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DASH_DIR = join(HERE, "..");
const REPO_ROOT = join(HERE, "..", "..", "..");

// Any function whose name matches one of these is treated as a React
// hook. We accept both PascalCase (useFoo) and the camelCase aliases
// some files in this repo use (useStateM, useEffectM, useMemoM).
const HOOK_NAMES = new Set([
  "useState",
  "useEffect",
  "useMemo",
  "useCallback",
  "useRef",
  "useReducer",
  "useContext",
  "useLayoutEffect",
  "useImperativeHandle",
  "useDebugValue",
  "useId",
  "useTransition",
  "useDeferredValue",
  "useSyncExternalStore",
  "useInsertionEffect",
  // Aliases used by this dashboard:
  "useStateM",
  "useEffectM",
  "useMemoM",
  "useCallbackM",
  "useRefM",
  "useReducerM",
  "useLayoutEffectM",
]);

// Callbacks we treat as "loops" — anything iterating an array. A hook
// in any of these is the violation we want to flag.
const LOOP_METHODS = new Set([
  "map",
  "forEach",
  "filter",
  "reduce",
  "reduceRight",
  "some",
  "every",
  "flatMap",
  "find",
  "findIndex",
]);

let failures = 0;
const fail = (msg) => {
  failures += 1;
  console.error("  ✗ " + msg);
};
const pass = (msg) => console.log("  ✓ " + msg);

const SUPPORTED_EXT = new Set([".js", ".jsx", ".ts", ".tsx"]);

function walk(dir, out) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    let st;
    try {
      st = statSync(full);
    } catch {
      continue;
    }
    if (st.isDirectory()) {
      if (entry === "node_modules" || entry === "__tests__" || entry.startsWith(".")) continue;
      walk(full, out);
    } else if (SUPPORTED_EXT.has(extname(entry))) {
      out.push(full);
    }
  }
  return out;
}

// For a node with ancestor chain, count how many ".map((x) => ...)"
// (or similar) callback bodies enclose it.
function loopCallbackDepth(node, ancestors) {
  let depth = 0;
  for (let i = ancestors.length - 2; i >= 0; i--) {
    const a = ancestors[i];
    if (a.type !== "CallExpression") continue;
    const c = a.callee;
    if (c.type !== "MemberExpression") continue;
    if (c.property.type !== "Identifier") continue;
    if (!LOOP_METHODS.has(c.property.name)) continue;
    // Last function-literal argument = iterator callback
    let cb = null;
    for (let b = a.arguments.length - 1; b >= 0; b--) {
      const arg = a.arguments[b];
      if (!arg) continue;
      if (arg.type === "ArrowFunctionExpression" || arg.type === "FunctionExpression") {
        cb = arg;
        break;
      }
    }
    if (cb && cb.start <= node.start && node.end <= cb.end) depth += 1;
  }
  return depth;
}

function checkFile(file) {
  const src = readFileSync(file, "utf8");
  let ast;
  try {
    ast = parse(src, {
      sourceType: "module",
      allowImportExportEverywhere: true,
      allowReturnOutsideFunction: true,
      plugins: ["jsx", "typescript", "classProperties", "classPrivateProperties"],
      errorRecovery: false,
    });
  } catch (e) {
    // If babel can't parse it, skip — not a JSX/TSX-aware lint target.
    return;
  }
  const violations = [];
  const ancestors = [];
  const visit = (node) => {
    if (!node || typeof node !== "object") return;
    ancestors.push(node);
    if (
      node.type === "CallExpression" &&
      node.callee.type === "Identifier" &&
      HOOK_NAMES.has(node.callee.name) &&
      loopCallbackDepth(node, ancestors) > 0
    ) {
      violations.push({
        name: node.callee.name,
        line: node.loc?.start?.line ?? "?",
        col: (node.loc?.start?.column ?? 0) + 1,
      });
    }
    for (const key of Object.keys(node)) {
      if (key === "loc" || key === "start" || key === "end" || key === "extra") continue;
      const val = node[key];
      if (Array.isArray(val)) {
        for (const v of val) {
          if (v && typeof v === "object" && typeof v.type === "string") visit(v);
        }
      } else if (val && typeof val === "object" && typeof val.type === "string") {
        visit(val);
      }
    }
    ancestors.pop();
  };
  visit(ast);
  const rel = file.replace(REPO_ROOT + "/", "");
  if (violations.length === 0) {
    pass(`${rel}: no hook-inside-loop violations`);
  } else {
    for (const v of violations) {
      fail(
        `${rel}:${v.line}:${v.col} — hook "${v.name}()" called inside an array-iteration callback (would cause React #310 when the array's length changes)`,
      );
    }
  }
}

const files = walk(DASH_DIR, []);
if (files.length === 0) {
  console.error("No source files found under", DASH_DIR);
  process.exit(2);
}
for (const f of files) checkFile(f);

if (failures > 0) {
  console.error(`\n${failures} violation(s).`);
  process.exit(1);
}
console.log("\nOK — no React hook called inside any array-iteration callback.");
