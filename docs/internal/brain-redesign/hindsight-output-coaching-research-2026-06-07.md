# Hindsight retain JSON-parse failure on gemma4-it-e4b-FLM — root cause + fix options

Date: 2026-06-07
Author: research agent (read-only investigation of CT 105 deployment)
Scope: Why Hindsight's retain fact-extraction stores 0 facts when the extraction
LLM is `gemma4-it-e4b-FLM`, but works with `gemma3-4b-FLM`. What no-code knobs,
patches, or model choices fix it.

Environment under study:
- Host: hal0 CT 105 (`ssh hal0`), service `hindsight-api.service`, user `hal0`.
- Package: `hindsight-api` **0.7.2** (latest upstream — PyPI confirms 0.7.2 is
  newest, released 2026-06-02). Installed at
  `/var/lib/hal0/memory/hindsight/.venv/lib/python3.12/site-packages/hindsight_api/`.
- Extraction LLM endpoint: lemonade/FastFlowLM at `http://127.0.0.1:13305/v1`,
  provider `openai`, model env `HINDSIGHT_API_LLM_MODEL`.
- Currently pinned back to `gemma3-4b-FLM` (working).

---

## 1. Root cause (file:line) — the `//` comments, not the fence

### The captured failing output (from journald)

gemma4 emits **exactly the schema the prompt requests** (it does NOT hallucinate),
wrapped in a ```json fence AND containing inline `//` comments:

```
```json
{
  "facts": [
    {
      "what": "Gemma4-E4B runs on the hal0 NPU ...",
      "when": "2026-06-07T15:28:28...",
      "where": "N/A",
      "who": "Gemma4-E4B",
      "why": "N/A",
      "fact_kind": "conversation",
      ...
      "relation_type": "caused_by" // Linking the wiring event to the system...
    }
  ]
}
```
```

`what/when/where/who/why/fact_kind/fact_type/entities` is the **documented
Hindsight schema** (extraction prompt, `fact_extraction.py` lines 503-557).
The deploy-doc's claim that gemma4 "uses a verbose non-conforming schema" /
hallucinated field names is **incorrect** — field names match. The two defects
are: (a) the markdown fence, (b) **inline `//` comments**. Only (b) is fatal.

### The active parse path (retain → provider, NOT the wrapper helper)

Retain extraction calls `LLMConfig.call(... response_format=schema,
skip_validation=True)` →
`engine/retain/fact_extraction.py::_extract_facts_from_chunk` (line ~1119) →
dispatches to the **OpenAI-compatible provider's own parse**, NOT
`llm_wrapper.parse_llm_json`.

`engine/providers/openai_compatible_llm.py`, in `call()`:

- **L520-530**: merges `extra_body` (`HINDSIGHT_API_LLM_EXTRA_BODY`) into the call.
- **L533-569**: builds `response_format`. With `strict_schema=False` (the default;
  retain never passes `strict_schema=True`) it takes the **soft-enforcement**
  branch: appends the JSON schema **as prose** to the system prompt and sets
  `response_format = {"type": "json_object"}`. **It never sends `json_schema`.**
- **L596-606**: strips `<think>`/reasoning tags, then `_strip_code_fences(content)`.
- **L607-611**: the failure:
  ```python
  clean_content = _strip_code_fences(content)
  try:
      json_data = json.loads(clean_content)        # ← fails on the // comment
  except json.JSONDecodeError:
      try:
          json_data = json.loads(content)          # ← fails on the leading ```fence
      except json.JSONDecodeError as json_err:
          ... retry (max_retries) ... then raise
  ```

### Exactly why it fails — reproduced empirically

`_strip_code_fences` (L56-73) **does** correctly strip the ```json fence — the
fence is NOT the blocker. After stripping, `clean_content` starts with `{`, then:

- `json.loads(clean_content)` → **`Expecting ',' delimiter`** at the `//` comment
  (stdlib `json` has no comment support).
- Fallback `json.loads(content)` runs on the RAW content (still fenced) →
  **`Expecting value: line 1 column 1 (char 0)`** — and *this* is the error that
  gets logged. So the logged "line 1 column 1" is the **fallback's** error; the
  true cause is the `//` comment defeating the first (clean) attempt.

Reproduced on CT 105 with the venv's own Python: clean attempt fails on the
comment, raw attempt fails on the fence — matching the logs byte-for-byte.

**Why gemma3-4b works:** it emits a clean ```json-fenced `{"facts":[...]}` with
**no `//` comments**, so after `_strip_code_fences` the first `json.loads`
succeeds. gemma4's only incremental sin is the inline comments.

---

## 2. No-code config knobs

Env knobs that exist (all `HINDSIGHT_API_*`, read in `config.py`):

| Knob | Effect on this bug |
|------|--------------------|
| `HINDSIGHT_API_LLM_EXTRA_BODY` (JSON, merged into every call, L967 wrapper / L520 provider) | Could carry `guided_json`/`grammar` IF the backend honored it. **FastFlowLM does not** (see below). Carrying `response_format` here is pointless — Hindsight already sets it. **No fix.** |
| `HINDSIGHT_API_LLM_REASONING_EFFORT` | Only applied to models Hindsight classifies as "reasoning". Irrelevant to comment emission. **No fix.** |
| `HINDSIGHT_API_RETAIN_MISSION` (documented; injected into the prompt at `fact_extraction.py` L894-900, *before* the guidelines) | **The one genuine no-code lever.** Plain-language steer text appended to the extraction prompt. You can inject: *"Output STRICT JSON only — no markdown code fences, no `//` or `/* */` comments, no trailing commas."* It "steers, does not replace." **Worth trying first, but unreliable:** it's the same soft-prompt channel gemma4 already overrides by adding comments, and the schema-is-already-in-the-prompt didn't stop it. Low cost, low confidence. |
| `HINDSIGHT_API_RETAIN_CUSTOM_INSTRUCTIONS` (mode=`custom` only) | Full prompt override. Same soft-prompt limitation; bigger blast radius. Not recommended for this. |
| `response_format` strict json_schema | **Not reachable by config** — retain hardcodes `strict_schema=False`; only `json_object` is sent, and FLM ignores even that. |

**Does Hindsight send `response_format` today?** Yes — `{"type":"json_object"}`
(soft mode). **FastFlowLM silently ignores it.** Per FastFlowLM's OpenAI-API docs,
`/v1/chat/completions` accepts only `model, messages, stream, temperature, top_p,
presence_penalty`. No `response_format`, no `json_schema`, no `guided_json`, no
grammar. So **no EXTRA_BODY grammar trick can force clean JSON on FLM** — the
parameter would be dropped on the floor.

**Verdict on no-code:** only `RETAIN_MISSION` prompt-coaching is available, and
it's a soft request a small model is free to ignore (gemma4 already does on
"emit JSON matching this schema"). Try it, but don't rely on it.

---

## 3. Prefill (seed the assistant turn)

- **No hook exists.** Grepped the entire `engine/` tree for
  `prefill|assistant.*seed|continue_final_message|add_generation_prompt|partial
  assistant` — zero hits. Hindsight always sends `[system, user]` and reads a
  fresh assistant completion. Prefill would require a code patch.
- **Even if patched, prefill is insufficient here.** Seeding `{"facts": [`
  suppresses the *opening* fence/prose, but does nothing about the **mid-output
  `//` comments**, which are the actual blocker. Prefill alone would NOT make
  gemma4 parse. Reject as a standalone fix.

---

## 4. Schema mismatch — there is none

The extraction prompt (`fact_extraction.py` L503-557) explicitly asks for
`what / when / where / who / why`, plus `fact_kind` (event|conversation) and
`fact_type` (world|assistant) and `entities`. gemma4's output uses exactly these
field names. **gemma4 followed the schema correctly.** A stricter
`response_format`/json_schema would not change field names (they're already
right) and FLM wouldn't honor it anyway. The schema is a non-issue; the comments
are the whole problem.

---

## 5. Patchability / upgrade path

- **Already on the newest upstream (0.7.2).** PyPI confirms 0.7.2 is the latest
  (2026-06-02); the bundled changelog only reaches 0.7.1. No newer release adds
  comment-tolerant parsing or `json_repair`. "Official-fix-first" is satisfied:
  there is **no upstream version to upgrade into** that fixes this.
- **Install is a plain (non-editable) pip wheel**, but the deployed `.py` files
  are `hal0:hal0` mode 664 — writable, so an in-place edit is physically
  possible. It would be **non-canonical and wiped on any reinstall/upgrade**, and
  the task says do-not-modify the deployed package, so treat any patch as a
  fork/overlay to be tracked in our repo and re-applied on deploy.

### The minimal tolerant-parse patch (if we choose to patch)

Single site: `engine/providers/openai_compatible_llm.py` **L607-611** (and the
identical block in `call_with_tools` path at ~L1106-1110 if retain ever uses
tools — it doesn't today). Add a third fallback after the two `json.loads`
attempts:

```python
except json.JSONDecodeError:
    try:
        from json_repair import repair_json
        json_data = json.loads(repair_json(clean_content))
    except Exception:
        json_data = json.loads(content)   # existing fallback / re-raise
```

Notes / guard-rails:
- **Use `json_repair` (or `json5`), NOT a naive `//`→EOL regex.** A regex
  strip corrupts `https://` and any `//` inside string values; `json_repair`
  handles comments, trailing commas, and fences robustly.
- **Neither `json_repair` nor `json5` is installed** in the venv (verified) — so
  this is a **dependency add + code edit**, both lost on reinstall.
- Invasiveness: ~5 lines at one site + one dependency. Low code risk, but it is a
  maintained fork burden against an upstream that re-emits the file on every
  `pip install -U`.

---

## Verdict: stay on gemma3-4b-FLM

Recommendation: **keep `HINDSIGHT_API_LLM_MODEL=gemma3-4b-FLM`.** Reasons:

1. gemma3-4b is **already green end-to-end** (retain+recall verified live) and
   emits clean fenced JSON that Hindsight parses today — zero maintenance.
2. There is **no robust no-code fix** for gemma4: FastFlowLM ignores
   `response_format` and supports no grammar/`guided_json`, so EXTRA_BODY can't
   force clean output, and `RETAIN_MISSION` prompt-coaching is a soft request the
   model already demonstrably overrides.
3. Making gemma4 work requires a **forked patch** (`json_repair` fallback at
   `openai_compatible_llm.py` L607-611) that must be re-applied on every
   upgrade — a standing burden for no functional gain over gemma3-4b.

Only revisit gemma4 if a *separate* reason forces it (e.g. gemma3-4b extraction
quality proves inadequate). If so, the cheapest viable route is the
`json_repair` fallback patch (Section 5), optionally combined with a
`RETAIN_MISSION` "strict JSON, no comments" steer to reduce how often the
fallback fires. Prefill and strict json_schema are dead ends on FastFlowLM.

### Cheap experiment ladder (if pursuing gemma4 later)
1. Set `HINDSIGHT_API_RETAIN_MISSION` with an explicit "no comments / no fences /
   no trailing commas, raw JSON only" clause; re-run retain; check if comments
   stop. (No code change. Low confidence.)
2. If still failing, apply the `json_repair` fallback patch + add the dep to the
   venv; track the patch in this repo as a deploy overlay.
3. Do NOT bother with EXTRA_BODY grammar or prefill — unsupported by FLM /
   insufficient against mid-output comments respectively.

---

## Source map (for re-verification)

- Service unit: `ssh hal0 'systemctl cat hindsight-api.service'`
- Failing logs: `ssh hal0 'journalctl -u hindsight-api.service | grep "JSON parse error"'`
- Parse failure site: `openai_compatible_llm.py::call` L607-611 (+ `_strip_code_fences` L56-73, response_format build L533-569).
- Retain caller: `fact_extraction.py::_extract_facts_from_chunk` L1067-1130; prompt L503-557; `_build_extraction_prompt_and_schema` + RETAIN_MISSION injection L876-929.
- Wrapper (different path, not used by retain): `llm_wrapper.py::parse_llm_json` L159-193; EXTRA_BODY env L967.
- Config envs: `config.py` — `ENV_LLM_EXTRA_BODY` L144, `ENV_LLM_REASONING_EFFORT` L141, `ENV_RETAIN_MISSION` L371, `ENV_RETAIN_EXTRACTION_MODE` L370.
- FastFlowLM param support: https://fastflowlm.com/docs/instructions/server/openapi/ (model, messages, stream, temperature, top_p, presence_penalty — no response_format/grammar).
- Latest hindsight-api version: PyPI 0.7.2 (2026-06-02) — already deployed.
