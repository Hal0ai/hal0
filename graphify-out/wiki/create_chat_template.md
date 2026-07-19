# create_chat_template

> 15 nodes

## Key Concepts

- **create_chat_template()** (9 connections) — `src/hal0/api/routes/chat_templates.py`
- **chat_templates.py** (8 connections) — `src/hal0/api/routes/chat_templates.py`
- **_catalog()** (7 connections) — `src/hal0/api/routes/chat_templates.py`
- **_templates_dir()** (5 connections) — `src/hal0/api/routes/chat_templates.py`
- **_render_check()** (4 connections) — `src/hal0/api/routes/chat_templates.py`
- **_entry()** (4 connections) — `src/hal0/api/routes/chat_templates.py`
- **Any** (4 connections)
- **list_chat_templates()** (4 connections) — `src/hal0/api/routes/chat_templates.py`
- **_TemplateBody** (3 connections) — `src/hal0/api/routes/chat_templates.py`
- **Path** (1 connections)
- **HTTP routes for the chat-template catalog.  Mounted under ``/api/chat-templates`** (1 connections) — `src/hal0/api/routes/chat_templates.py`
- **Best-effort render lint for a chat template.      Returns ``None`` when the temp** (1 connections) — `src/hal0/api/routes/chat_templates.py`
- **Build the full catalog: ``auto`` first, then store entries sorted by id.** (1 connections) — `src/hal0/api/routes/chat_templates.py`
- **Return all available chat templates.** (1 connections) — `src/hal0/api/routes/chat_templates.py`
- **Write a custom chat template to the model store.      The ``id`` must match ``[a** (1 connections) — `src/hal0/api/routes/chat_templates.py`

## Relationships

- [model_store_root](model_store_root.md) (1 shared connections)
- [BaseModel](BaseModel.md) (1 shared connections)
- [BadRequest](BadRequest.md) (1 shared connections)
- [errors.py](errors.py.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/chat_templates.py`

## Audit Trail

- EXTRACTED: 51 (94%)
- INFERRED: 3 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*