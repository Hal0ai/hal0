# profiles.py

> 24 nodes · cohesion 0.11

## Key Concepts

- **profiles.py** (11 connections) — `src/hal0/api/routes/profiles.py`
- **create_profile()** (8 connections) — `src/hal0/api/routes/profiles.py`
- **import_profile_route()** (8 connections) — `src/hal0/api/routes/profiles.py`
- **ProfileBody** (6 connections) — `src/hal0/api/routes/profiles.py`
- **Any** (6 connections)
- **delete_profile()** (5 connections) — `src/hal0/api/routes/profiles.py`
- **export_profile()** (5 connections) — `src/hal0/api/routes/profiles.py`
- **ProfileUpdateBody** (5 connections) — `src/hal0/api/routes/profiles.py`
- **get_profile()** (4 connections) — `src/hal0/api/routes/profiles.py`
- **list_profiles()** (4 connections) — `src/hal0/api/routes/profiles.py`
- **Request** (4 connections)
- **BaseModel** (2 connections)
- **.image_nonempty()** (1 connections) — `src/hal0/api/routes/profiles.py`
- **.name_kebab()** (1 connections) — `src/hal0/api/routes/profiles.py`
- **.image_nonempty()** (1 connections) — `src/hal0/api/routes/profiles.py`
- **Profile catalog endpoints.  Mounted under /api/profiles:      GET    ""** (1 connections) — `src/hal0/api/routes/profiles.py`
- **Return every profile in the catalog as a JSON array.      Each item shape::** (1 connections) — `src/hal0/api/routes/profiles.py`
- **Create a custom profile.      Returns the created profile item (same shape as li** (1 connections) — `src/hal0/api/routes/profiles.py`
- **Import a profile from an uploaded ``.hal0profile.json`` envelope.      Body::** (1 connections) — `src/hal0/api/routes/profiles.py`
- **Resolve a single profile by name.      Returns the profile item (same shape as l** (1 connections) — `src/hal0/api/routes/profiles.py`
- **Serialize a profile into its portable ``.hal0profile.json`` envelope.      Embed** (1 connections) — `src/hal0/api/routes/profiles.py`
- **Delete a custom profile.      Raises:         409 profiles.seed_immutable: name** (1 connections) — `src/hal0/api/routes/profiles.py`
- **Body for POST /api/profiles and PUT /api/profiles/{name}.** (1 connections) — `src/hal0/api/routes/profiles.py`
- **Body for PUT /api/profiles/{name} — name is taken from the URL.** (1 connections) — `src/hal0/api/routes/profiles.py`

## Relationships

- [ProfileCatalog](ProfileCatalog.md) (10 shared connections)
- [record_action](record_action.md) (3 shared connections)
- [ProfileConfig](ProfileConfig.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [BadRequest](BadRequest.md) (1 shared connections)
- [_profile](_profile.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/profiles.py`

## Audit Trail

- EXTRACTED: 67 (84%)
- INFERRED: 13 (16%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*