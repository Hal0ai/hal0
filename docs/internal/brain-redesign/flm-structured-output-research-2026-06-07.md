# FLM structured-output research — can gemma4-it-e4b-FLM be coaxed to emit valid extraction JSON?

Date: 2026-06-07
Env: CT 105 (`ssh hal0`). Server: **Lemonade `lemond` v10.6.0** OpenAI frontend at
`http://127.0.0.1:13305/v1`, fronting **FastFlowLM (FLM) v0.9.43** models via the `flm`
recipe (NPU). Host `flm` binary: `/usr/bin/flm`.
Goal: make Hindsight's `fact_extraction` `json.loads` succeed (currently gemma4 emits a
` ```json `-fenced object — sometimes with `//` comments — and `json.loads` fails 4×, storing 0 facts).

## TL;DR verdict

**Assistant prefill is the only working lever, and it works.** Seeding a trailing
`{"role":"assistant","content":"{\"facts\": ["}` makes gemma4-it-e4b-FLM continue the JSON
instead of opening a ` ```json ` fence — verified live, parses clean. `response_format`,
`json_schema`, and `grammar` are **silently ignored** by the FLM recipe (not just gemma4 —
the whole NPU recipe). Stop sequences are honored but don't help with the fence.

**Recommendation:** fix Hindsight, don't just coax the model. Two compounding fixes, cheapest first:
1. **Parser-side (do this regardless):** Hindsight already strips the ` ``` ` fence for
   gemma3-4b-FLM; extend the same sanitizer to also strip `//`/`/* */` comments before
   `json.loads`. This is the dependable fix and is model-agnostic.
2. **Prefill (optional hardening):** if Hindsight's LLM client exposes the messages array,
   append an assistant prefill turn `{"facts": [` to kill the fence at the source. Caveat below.

The single most important architectural fact: **this is Lemonade fronting FLM, not FLM's
native server.** Lemonade's structured-output features (response_format → grammar) are
implemented in the **llamacpp recipe only** (llama.cpp GBNF). The `flm` NPU recipe gets none
of them — Lemonade accepts the params and drops them on the floor.

## Lever-by-lever findings (with test evidence)

### 1. `response_format` (json_object / json_schema) — DOES NOT WORK ❌
FLM recipe silently ignores it. Tested on `qwen3-it-4b-FLM` (fast probe; result is
recipe-level so it generalizes to all `flm`-recipe models incl. gemma4):

| Request | Output |
|---|---|
| no `response_format` | clean `{"facts": [...]}` JSON object |
| `response_format:{type:json_object}` | `Facts extracted:\n- The user moved...` (markdown bullets, **not JSON**) |
| `response_format:{type:json_schema, strict:true, schema:{...}}` | identical markdown bullets |

Not only is it ignored — its *presence* didn't constrain anything; output was prose. No
400, no error: accepted-and-discarded. Confirms the deploy-doc note (`qwen3-it-4b-FLM
IGNORES response_format`) still holds on the 0.9.43 build under Lemonade 10.6.0.
Lemonade's own OpenAI API doc does not list `response_format` as a supported param.

### 2. Assistant prefill — WORKS ✅ (the answer)
FLM recipe honors the OpenAI prefill convention: a trailing `assistant` message is treated
as a partial turn the model continues. **FLM returns only the generated continuation, not
the prefilled prefix** — the caller reconstructs `prefix + content`.

gemma4-it-e4b-FLM, same extraction prompt:
- **Baseline (no prefill):** `` ```json\n{\n  "facts": [...]\n}\n``` `` → `json.loads` FAILS
  (`Expecting value: line 1 column 1`). Reproduces the live bug. (~15s)
- **With prefill** `{"role":"assistant","content":"{\"facts\": ["}`:
  returned content = `"User moved to Berlin in 2021", "...nurse at Charite hospital", "...dog named Max"\n]}`
  — **no fence, no comments.** `json.loads("{\"facts\": [" + content)` → **OK**. (~11s)

Also verified on qwen3-it-4b-FLM (clean continuation, `{"facts": [...]}` parses).

**Caveat (important, found live):** prefix-continuation is prompt-sensitive. When the user
prompt literally contained the string `{"facts": [...]}`, gemma4 **re-emitted its own
`{"facts": [`**, so blind `prefix + content` concatenation produced a doubled prefix and a
malformed object. Robust reconstruction must be defensive:
```python
content = resp.choices[0].message.content
prefix  = '{"facts": ['
for candidate in (content, prefix + content):     # try as-is, then prepend prefix
    try: facts = json.loads(candidate)["facts"]; break
    except Exception: continue
```
Also: gemma4 sometimes drifts to a wrong nested schema (`[{"fact": "..."}]`) when the prompt
doesn't pin the schema. Prefill fixes the *fence/comments* (the `json.loads` crash); it does
NOT guarantee schema shape — keep the prompt's schema instruction explicit and have Hindsight
tolerate/normalize the value shape.

### 3. Grammar / constrained decoding — NOT AVAILABLE ❌
- `flm --help` / `flm serve --help` (v0.9.43): **no** `--grammar`, `--json`, or
  constrained-decoding flag. The `-j/--json` flag only formats CLI output for
  `list/validate/version`; it is NOT a generation constraint.
- The `grammar` request param (llama.cpp-style) sent to lemond on the FLM recipe was
  **ignored** (same prose-bullets output as the response_format tests).
- Upstream: Lemonade issue #1759 ("Expose llama.cpp grammar parameter") targets the
  **llamacpp recipe** — grammar/GBNF is a llama.cpp feature. The FLM NPU backend has no
  grammar/constrained-decode path. So there is no official grammar lever for FLM models.

### 4. Stop sequences — HONORED, but don't solve the fence ⚠️
`stop` is respected (`finish_reason: stop` returned). But the fence opens at the *start*
of generation (` ```json ` is the first token), so a stop sequence can't strip a leading
fence, and inline `//` comments appear mid-array where a stop would truncate the JSON.
Useful only as a guard against trailing chatter when combined with prefill (e.g. `stop:["]}"]`
then re-append `]}`), and even that interacts badly with the prefix-echo caveat above — not
recommended as a primary mechanism.

## Per-model notes
- **gemma4-it-e4b-FLM** — the target. ~11–15s/call here (faster than the ~2min the brief
  warned of; current load was warm). Baseline emits the fence; prefill fixes it. Confirmed live.
- **gemma3-4b-FLM** — per deploy doc, already works because Hindsight strips the fence. Lower
  risk than gemma4. If prefill is not wired into Hindsight, **staying on gemma3-4b-FLM +
  fence/comment-stripping parser is the lowest-effort reliable path.**
- **qwen3-it-4b-FLM** — used as the fast probe. On THIS build it returned clean
  `{"facts": [...]}` at baseline (no bare-list, contrary to the older 0.7.2 finding) but
  degraded to prose when `response_format` was set. Failure modes differ per model, so the
  fence/comment behavior of gemma4 was confirmed on gemma4 directly, not extrapolated.

## Bottom line
gemma4-it-e4b-FLM CAN be made to emit `json.loads`-able extraction JSON, but **only via
assistant prefill**, and only with defensive prefix reconstruction in the caller —
`response_format`/`json_schema`/`grammar` are dead ends on the FLM/NPU recipe. The robust,
recommended fix is parser-side fence+comment stripping in Hindsight (works for any FLM model
including the already-green gemma3-4b-FLM); prefill is a worthwhile additional hardening if
Hindsight's LLM client lets you append an assistant turn.

## Sources
- FLM CLI `flm --help` / `flm serve --help` (v0.9.43, live on CT 105)
- Lemonade OpenAI API doc — https://lemonade-server.ai/docs/api/openai/
- Lemonade grammar feature request (llamacpp-scoped) — https://github.com/lemonade-sdk/lemonade/issues/1759
- FLM OpenAI API doc — https://fastflowlm.com/docs/instructions/server/openapi/
- llama.cpp response_format/grammar behavior — https://github.com/ggml-org/llama.cpp/issues/11847, /10732
- Live tests against `http://127.0.0.1:13305/v1` on CT 105, 2026-06-07 (NPU unloaded after).
