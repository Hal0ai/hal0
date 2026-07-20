# detect.py

> 18 nodes

## Key Concepts

- **detect.py** (9 connections) — `src/hal0/registry/detect.py`
- **_heuristic_only()** (9 connections) — `src/hal0/registry/detect.py`
- **quant_from_filename()** (7 connections) — `src/hal0/registry/detect.py`
- **_hf_repo_name_from_path()** (5 connections) — `src/hal0/registry/detect.py`
- **_filename_capability()** (5 connections) — `src/hal0/registry/detect.py`
- **quant_from_rocmfpx_filename()** (4 connections) — `src/hal0/registry/detect.py`
- **quant_from_file_type()** (4 connections) — `src/hal0/registry/detect.py`
- **DetectionResult** (4 connections) — `src/hal0/registry/detect.py`
- **Path** (3 connections)
- **Any** (1 connections)
- **Model detection — derive backends + capabilities from a file on disk.  Pure insp** (1 connections) — `src/hal0/registry/detect.py`
- **Quant label from a filename token, or ``None`` when nothing matches.      Matche** (1 connections) — `src/hal0/registry/detect.py`
- **ROCmFPX-family quant label from a filename, or ``None`` when no hit.      Prefer** (1 connections) — `src/hal0/registry/detect.py`
- **Map a GGUF ``general.file_type`` value to a quant label.      Tolerates the LLAM** (1 connections) — `src/hal0/registry/detect.py`
- **Walk up the path looking for ``models--ORG--REPO`` (HF cache layout).      Retur** (1 connections) — `src/hal0/registry/detect.py`
- **Outcome of a single-file detection pass.      ``raw_hints`` carries provider-spe** (1 connections) — `src/hal0/registry/detect.py`
- **Best-effort capability inferred from filename tokens. ``None`` if no hit.      D** (1 connections) — `src/hal0/registry/detect.py`
- **Fallback detection: filename heuristic, no header read.** (1 connections) — `src/hal0/registry/detect.py`

## Relationships

- [detect](detect.md) (9 shared connections)
- [plan_fileset](plan_fileset.md) (2 shared connections)
- [models_service.py](models_service.py.md) (1 shared connections)
- [_guess_capability](_guess_capability.md) (1 shared connections)

## Source Files

- `src/hal0/registry/detect.py`

## Audit Trail

- EXTRACTED: 55 (93%)
- INFERRED: 4 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*