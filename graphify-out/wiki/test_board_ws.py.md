# test_board_ws.py

> 12 nodes

## Key Concepts

- **test_board_ws.py** (9 connections) — `tests/board/test_board_ws.py`
- **_app_with_store()** (7 connections) — `tests/board/test_board_ws.py`
- **_store()** (6 connections) — `tests/board/test_board_ws.py`
- **test_since_absent_streams_only_new()** (4 connections) — `tests/board/test_board_ws.py`
- **FastAPI** (3 connections)
- **test_since_zero_replays_existing_events()** (3 connections) — `tests/board/test_board_ws.py`
- **test_board_filter()** (3 connections) — `tests/board/test_board_ws.py`
- **test_frame_shape()** (3 connections) — `tests/board/test_board_ws.py`
- **test_close_is_clean_when_no_store()** (3 connections) — `tests/board/test_board_ws.py`
- **board_ws.py — local card_event streamer for /api/board/events.  hal0 owns the bo** (1 connections) — `tests/board/test_board_ws.py`
- **No ?since= ⇒ start at the latest cursor; a mutation AFTER connect streams.** (1 connections) — `tests/board/test_board_ws.py`
- **No store on app.state and no way to build one under an isolated path:     the br** (1 connections) — `tests/board/test_board_ws.py`

## Relationships

- [BoardStore](BoardStore.md) (2 shared connections)

## Source Files

- `tests/board/test_board_ws.py`

## Audit Trail

- EXTRACTED: 44 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*