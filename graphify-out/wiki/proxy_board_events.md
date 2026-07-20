# proxy_board_events

> 13 nodes · cohesion 0.21

## Key Concepts

- **proxy_board_events()** (7 connections) — `src/hal0/api/routes/board_ws.py`
- **_ensure_store()** (6 connections) — `src/hal0/api/routes/board_ws.py`
- **board_events_ws()** (4 connections) — `src/hal0/api/routes/board.py`
- **board_ws.py** (4 connections) — `src/hal0/api/routes/board_ws.py`
- **_start_cursor()** (4 connections) — `src/hal0/api/routes/board_ws.py`
- **WebSocket** (3 connections)
- **.poll()** (3 connections) — `tests/comfyui/test_provision.py`
- **WebSocket** (1 connections)
- **Stream the local ``card_event`` feed to the browser.      The browser passes ``s** (1 connections) — `src/hal0/api/routes/board.py`
- **Operator Board events-WS — streams the hal0-local ``card_event`` feed.  hal0 own** (1 connections) — `src/hal0/api/routes/board_ws.py`
- **Return the app-state BoardStore, building + first-boot-initialising it on     fi** (1 connections) — `src/hal0/api/routes/board_ws.py`
- **Resolve the initial cursor from ``?since=``.      Absent/blank ⇒ start at the la** (1 connections) — `src/hal0/api/routes/board_ws.py`
- **Stream local board events to an already-accepted browser WS.      Named ``proxy_** (1 connections) — `src/hal0/api/routes/board_ws.py`

## Relationships

- [provision_comfyui_downloads](provision_comfyui_downloads.md) (2 shared connections)
- [record_action](record_action.md) (1 shared connections)
- [BoardStore](BoardStore.md) (1 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/board.py`
- `src/hal0/api/routes/board_ws.py`
- `tests/comfyui/test_provision.py`

## Audit Trail

- EXTRACTED: 30 (81%)
- INFERRED: 7 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*