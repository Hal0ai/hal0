# read_gguf_header

> 25 nodes

## Key Concepts

- **read_gguf_header()** (25 connections) — `src/hal0/registry/gguf_header.py`
- **_Reader** (11 connections) — `src/hal0/registry/gguf_header.py`
- **_read_value()** (10 connections) — `src/hal0/registry/gguf_header.py`
- **gguf_header.py** (8 connections) — `src/hal0/registry/gguf_header.py`
- **GGUFParseError** (8 connections) — `src/hal0/registry/gguf_header.py`
- **_skip_value()** (8 connections) — `src/hal0/registry/gguf_header.py`
- **.read()** (7 connections) — `src/hal0/registry/gguf_header.py`
- **.u64()** (6 connections) — `src/hal0/registry/gguf_header.py`
- **.gguf_string()** (6 connections) — `src/hal0/registry/gguf_header.py`
- **.u32()** (5 connections) — `src/hal0/registry/gguf_header.py`
- **_suppress_close** (5 connections) — `src/hal0/registry/gguf_header.py`
- **mmap** (3 connections)
- **.skip()** (3 connections) — `src/hal0/registry/gguf_header.py`
- **.__init__()** (2 connections) — `src/hal0/registry/gguf_header.py`
- **Any** (2 connections)
- **Path** (1 connections)
- **.__enter__()** (1 connections) — `src/hal0/registry/gguf_header.py`
- **.__exit__()** (1 connections) — `src/hal0/registry/gguf_header.py`
- **GGUF header parser — read-only metadata extraction.  Parses the GGUF v1-v3 magic** (1 connections) — `src/hal0/registry/gguf_header.py`
- **Raised on a truncated / malformed GGUF header.      Callers usually convert this** (1 connections) — `src/hal0/registry/gguf_header.py`
- **Cursor over a bytes-like blob with bounds-checked reads.** (1 connections) — `src/hal0/registry/gguf_header.py`
- **Read and return a GGUF value of ``vtype``. Used for keys we want.** (1 connections) — `src/hal0/registry/gguf_header.py`
- **Skip a GGUF value of ``vtype`` without materialising it.** (1 connections) — `src/hal0/registry/gguf_header.py`
- **Parse the GGUF header and return a dict of interesting KV pairs.      Returns ``** (1 connections) — `src/hal0/registry/gguf_header.py`
- **Context manager that swallows OSError on close — keeps cleanup quiet.** (1 connections) — `src/hal0/registry/gguf_header.py`

## Relationships

- [detect](detect.md) (12 shared connections)
- [AgentMCPClient](AgentMCPClient.md) (1 shared connections)

## Source Files

- `src/hal0/registry/gguf_header.py`

## Audit Trail

- EXTRACTED: 107 (90%)
- INFERRED: 12 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*