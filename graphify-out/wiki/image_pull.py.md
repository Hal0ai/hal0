# image_pull.py

> 13 nodes

## Key Concepts

- **image_pull.py** (5 connections) — `src/hal0/slots/image_pull.py`
- **ImagePullJob** (5 connections) — `src/hal0/slots/image_pull.py`
- **run_image_pull()** (5 connections) — `src/hal0/slots/image_pull.py`
- **resolve_slot_image()** (4 connections) — `src/hal0/slots/image_pull.py`
- **Any** (3 connections)
- **inspect_image_state()** (3 connections) — `src/hal0/slots/image_pull.py`
- **.as_dict()** (2 connections) — `src/hal0/slots/image_pull.py`
- **.__init__()** (1 connections) — `src/hal0/slots/image_pull.py`
- **Container-image pull orchestration for slots (extracted from routes/slots.py).** (1 connections) — `src/hal0/slots/image_pull.py`
- **Lightweight job object for a container-image pull.      Tracks state (pulling |** (1 connections) — `src/hal0/slots/image_pull.py`
- **Run the container pull in background, updating ``job`` per line.      Writes pro** (1 connections) — `src/hal0/slots/image_pull.py`
- **Resolve slot ``name``'s container image via its profile, or None.      Fail-soft** (1 connections) — `src/hal0/slots/image_pull.py`
- **Return "present" | "missing" for ``image`` (fail-soft → "missing").** (1 connections) — `src/hal0/slots/image_pull.py`

## Relationships

- [SlotState](SlotState.md) (2 shared connections)
- [load_profiles_config](load_profiles_config.md) (1 shared connections)

## Source Files

- `src/hal0/slots/image_pull.py`

## Audit Trail

- EXTRACTED: 30 (91%)
- INFERRED: 3 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*