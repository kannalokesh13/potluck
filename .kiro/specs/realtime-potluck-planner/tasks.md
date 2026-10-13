# Implementation Plan: Realtime Potluck Planner

## Overview

This plan delivers a complete, working application (no stubs): a Python FastAPI + uvicorn backend
with REST endpoints and WebSocket realtime fan-out, an in-memory `BoardStore`, a `PotluckService`
holding the business logic, and a `WebSocketHub` broadcaster. The frontend is a browser UI served as
static files implementing the PIRNAV "Potluck Week 2026" festive theme: a landing hero with the banner
image and feature tiles, a Status Board of item cards, a People tab, add-item and add-your-name/claim
flows, and a copyable share link.

Tasks are ordered to build bottom-up (models → store → service → hub → API → frontend → tests → docs),
with each step building on the previous ones and wiring everything together so there is no orphaned code.
Property-based tests (Hypothesis) cover all 8 Correctness Properties from the design, and an integration
test exercises realtime fan-out between two WebSocket clients.

## Tasks

- [x] 1. Set up project structure, dependencies, and core data models
  - Create the backend package layout (`app/`, `app/models.py`, `app/store.py`, `app/service.py`, `app/hub.py`, `app/api.py`, `app/main.py`) and a `tests/` directory
  - Create `requirements.txt` pinning fastapi, uvicorn[standard], websockets, pytest, hypothesis, httpx
  - Implement `ItemStatus` enum, `Item` dataclass with derived `status` property, `Board`, `PersonContribution`, `BoardState`, `ChangeType` enum, and `ChangeEvent` dataclass per the design Data Models
  - Define `ConflictError`, `NotFoundError`, and `PermissionError`-style custom exceptions used by the service layer
  - _Requirements: 1.1, 4.1_

- [x] 2. Implement the in-memory BoardStore
  - [x] 2.1 Implement `InMemoryBoardStore` with `get_board`, `create_board`, `save_item`, `get_item`, `list_items`
    - Store boards and items in dict structures keyed by board id and item id
    - Generate unguessable board ids and unique item ids (e.g., `secrets.token_urlsafe` / `uuid4`)
    - Provide a per-item lock so claim read-modify-write can be made atomic by the service
    - _Requirements: 1.2, 6.1_

  - [x]* 2.2 Write unit tests for BoardStore
    - Test create/get board lazily, save/get/list items, uniqueness of ids, not-found returns None
    - _Requirements: 1.2, 6.1_

- [x] 3. Implement the WebSocketHub broadcaster
  - [x] 3.1 Implement `WebSocketHub` with `subscribe`, `unsubscribe`, `broadcast`
    - Track active connections grouped by board id and serialize `ChangeEvent` to JSON
    - Push events to all subscribers of the affected board and clean up dropped connections
    - _Requirements: 3.1_

  - [x]* 3.2 Write unit tests for WebSocketHub using a fake connection
    - Verify a broadcast reaches all subscribers of a board and none of other boards; dropped connections are removed
    - _Requirements: 3.1_

- [x] 4. Implement the PotluckService business logic
  - [x] 4.1 Implement `add_item` with title trimming/validation and single `ItemAdded` broadcast
    - Lazily create the board if missing; reject empty-trimmed title (raise validation error, no state change, no broadcast)
    - Associate optional `image_url`; return the new Available item
    - _Requirements: 1.1, 1.2, 1.3, 7.1, 7.3_

  - [x]* 4.2 Write property test for add_item
    - **Property 4: Add preserves others** — add_item creates exactly one new Available item and modifies no existing item
    - **Validates: Requirements 1.1**

  - [x] 4.3 Implement `claim_item` with atomic per-item read-modify-write and conflict handling
    - Reject empty-trimmed name (validation); raise `NotFoundError` for missing item; raise `ConflictError` with current claimer when already taken (no broadcast)
    - On success set trimmed `claimed_by`, derive Already_Taken, emit exactly one `ItemClaimed`
    - _Requirements: 2.1, 2.2, 7.2, 7.3, 7.4_

  - [x]* 4.4 Write property test for claim status consistency and single claimer
    - **Property 1: Status consistency** — status is Already_Taken iff claimed_by is a non-empty name, else Available
    - **Property 2: Single claimer** — at most one participant is claimed_by for any item at any time
    - **Validates: Requirements 4.1, 2.1**

  - [x]* 4.5 Write property test for claim exclusivity (no double booking)
    - **Property 3: Claim exclusivity** — for concurrent claims on the same Available item, exactly one succeeds and the other raises ConflictError; final claimed_by is the winner's name
    - **Validates: Requirements 2.2**

  - [x] 4.6 Implement `unclaim_item` with ownership check
    - Succeed only when `claimed_by` matches the trimmed name (case-insensitive); otherwise raise forbidden/permission error (no state change, no broadcast)
    - On success unset claimer, derive Available, emit exactly one `ItemUnclaimed`
    - _Requirements: 2.3, 2.4, 7.2_

  - [x]* 4.7 Write property test for unclaim ownership
    - **Property 8: Unclaim ownership** — unclaim succeeds only when claimed_by matches the name; afterwards the item is Available
    - **Validates: Requirements 2.3**

  - [x] 4.8 Implement `list_people` and `get_board_state`
    - Group claimed items by claimer name (case-insensitive key, original casing preserved); exclude unclaimed items
    - `get_board_state` returns items plus grouped people as the Board_Snapshot
    - _Requirements: 5.1, 5.2, 3.2_

  - [x]* 4.9 Write property test for people-view completeness and soundness
    - **Property 5: People-view completeness and soundness** — the multiset of items across all PersonContribution entries equals exactly the set of claimed items, with no unclaimed item present
    - **Validates: Requirements 5.1**

  - [x]* 4.10 Write unit tests for broadcast counting on success and failure
    - **Property 6: Realtime fan-out** — exactly one ChangeEvent per successful mutation, none for failed mutations, using a fake hub
    - **Validates: Requirements 3.1**

- [x] 5. Checkpoint - core backend logic
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement the REST API and WebSocket endpoint
  - [x] 6.1 Implement REST routes on FastAPI wiring store, service, and hub together
    - `GET /board/{board_id}/state`, `POST /board/{board_id}/items`, `POST /board/{board_id}/items/{item_id}/claim`, `POST /board/{board_id}/items/{item_id}/unclaim`, `GET /board/{board_id}/people`
    - Map errors to HTTP status: 400 validation, 404 not-found, 409 conflict (include current claimer), 403 forbidden
    - _Requirements: 1.1, 2.1, 2.2, 2.3, 2.4, 3.2, 5.1, 7.5, 7.6, 7.7, 7.8_

  - [x] 6.2 Implement the WebSocket subscribe endpoint
    - `WS /board/{board_id}/ws` registers the connection with the hub, acks subscription, streams `ChangeEvent`s, and unsubscribes on disconnect
    - _Requirements: 3.1, 3.4, 6.4_

  - [x]* 6.3 Write API unit tests with httpx/TestClient
    - Cover 201/200 success shapes and 400/404/409/403 error statuses for each endpoint
    - _Requirements: 7.5, 7.6, 7.7, 7.8_

- [x] 7. Implement the backend entrypoint and static file serving
  - [x] 7.1 Implement `app/main.py` to create the FastAPI app, mount the API routes, and serve the frontend
    - Serve `static/` (including `static/images/potluck-banner.png`) and the SPA entry `index.html`; expose a route so `GET /board/{board_id}` returns the UI
    - Configure uvicorn runnable via `python -m app.main` or `uvicorn app.main:app`
    - _Requirements: 6.1, 6.2, 6.4_

- [x] 8. Checkpoint - runnable server with REST + WebSocket
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Implement the festive theme stylesheet and shared frontend client
  - [x] 9.1 Create `static/css/theme.css` implementing the PIRNAV "Potluck Week 2026" theme
    - Define CSS variables for the palette (marigold orange primary, deep maroon banners/headers, warm cream background, accent green success, turmeric/gold accent, grey available)
    - Style the banner/hero, date pill, taglines, feature tiles, item cards, and status badges (green "Already Taken" / grey "Available")
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 9.2 Create `static/js/api.js` and `static/js/realtime.js` client modules
    - REST helpers for state/add/claim/unclaim/people; WebSocket client that fetches snapshot first, subscribes, applies ChangeEvents, and reconnects with backoff then re-fetches snapshot
    - _Requirements: 3.2, 3.3, 3.5_

- [x] 10. Implement the frontend views and interactions
  - [x] 10.1 Build the landing/hero section in `index.html` with the banner image and feature tiles
    - Reference `static/images/potluck-banner.png` as the main hero image; show brand, event title, dates badge, taglines, and four feature tiles (Add Food Items, Add Your Name, Live Item Status, Share with Office) with category motifs
    - _Requirements: 8.1, 8.2, 8.3, 8.6_

  - [x] 10.2 Build the Status Board view rendering item cards from live state
    - Render each item as a card with image, title, bringer label ("Bringing: <name>" or "Bringing: Not assigned"), and green/grey status badge; update live on ChangeEvents
    - _Requirements: 1.4, 2.5, 4.2, 4.3_

  - [x] 10.3 Build the Add Item form and the Add-your-name/claim/unclaim flow
    - Add-item posts title + optional image; claim posts the participant name; unclaim available only to the claimer; surface 400/404/409/403 errors inline
    - _Requirements: 1.1, 1.3, 2.1, 2.2, 2.3, 2.4, 7.1, 7.2_

  - [x] 10.4 Build the People tab and the copyable share link
    - People tab lists each participant with the items they are bringing; share control copies the current board URL to the clipboard
    - _Requirements: 5.3, 6.3_

- [x] 11. Checkpoint - full UI wired to live backend
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Write the realtime fan-out and convergence integration tests
  - [x]* 12.1 Write an integration test connecting two WebSocket clients to the same board
    - Perform add/claim on one client and assert the other receives the matching ChangeEvent and converges to the same snapshot
    - **Property 6: Realtime fan-out** and **Property 7: Snapshot/event convergence**
    - **Validates: Requirements 3.1, 3.3**

- [x] 13. Add README with run instructions
  - [x] 13.1 Write `README.md`
    - Document installing dependencies, starting the server (uvicorn command), opening a board URL, how a share link works, and where to drop the banner file (`static/images/potluck-banner.png`)
    - _Requirements: 6.1, 6.2, 6.3_

- [x] 14. Final checkpoint - ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

### Enhancement: adder details, Employee ID and delete (Requirements 9 to 14)

Tasks 15 onward implement the enhancement (Requirements 9 to 14 plus the amended criteria 1.1, 2.6, 3.1, 4.3, 5.4, 7.3, 7.6) following `design.md`. Unlike tasks 1 to 14, **no test sub-task in this section is optional**: the `add_item` signature change breaks existing tests, so they are updated in the same sweep (task 19.1) and the new tests are part of the deliverable. The backend is built bottom-up (models, stores, service, API), then tests, then Postgres tests, then the frontend client and UI, then docs. The existing test suite is red between task 17.1 and task 19.1 only because of the signature change; the application itself stays importable and runnable after every backend task.

- [x] 15. Models and public serialization
  - [x] 15.1 Extend the data models in `app/models.py`
    - Add `added_by: str | None = None`, `employee_id: str | None = field(default=None, repr=False)` and `bring_date: str | None = None` to `Item`, appended after the existing fields with defaults so every positional `Item(...)` construction keeps working
    - Add `ChangeType.ITEM_DELETED = "ItemDeleted"`; `ChangeEvent` is unchanged
    - Keep `status` derived from `claimed_by` only
    - _Requirements: 9.6, 10.5, 11.4, 12.12, 13.1, 14.1_

  - [x] 15.2 Make `item_to_dict` in `app/hub.py` an allow-list serializer
    - Emit exactly `id, board_id, title, image_url, claimed_by, created_at, status, added_by, bring_date`; never emit `employee_id` (no `asdict` minus deny-list)
    - Confirm every REST/WS path (`event_to_dict`, board snapshot, people view, add/claim/unclaim responses) goes through `item_to_dict` so `ItemDeleted` events have the same `{type, board_id, item}` shape
    - _Requirements: 10.5, 10.6, 11.1, 11.2, 12.12_

  - [x] 15.3 Write serialization unit tests in `tests/test_hub.py`
    - `item_to_dict` has exactly the expected key set and no `employee_id` key or value; legacy items give `None` for `added_by` and `bring_date`
    - `repr(Item)` and `repr` of `BoardState`/`PersonContribution` never contain the employee id
    - `event_to_dict` for `ItemDeleted` has the shape `{type, board_id, item}` with public fields only
    - _Requirements: 10.5, 10.6, 11.1, 11.2, 13.2_

- [x] 16. Store layer: delete and Postgres migration
  - [x] 16.1 Add `delete_item` to the `BoardStore` protocol and `InMemoryBoardStore` in `app/store.py`
    - `delete_item(board_id, item_id) -> bool` removes exactly one item, returns `True` when removed and `False` otherwise, preserves the relative order of the remaining items and never touches other boards
    - Never remove the entry from the per-item lock table on delete (a waiting claimer may still hold that lock object)
    - The new fields round-trip unchanged through `save_item`/`get_item`/`list_items`
    - _Requirements: 12.7, 12.16, 14.1, 14.3, 14.4_

  - [x] 16.2 Update `PostgresBoardStore` in `app/pg_store.py`: migration, new columns, delete
    - Add `_MIGRATION_STATEMENTS` (`ALTER TABLE potluck_items ADD COLUMN IF NOT EXISTS added_by text, ... employee_id text, ... bring_date text`) and `_ITEM_COLUMNS`
    - `ensure_schema` runs the existing schema statements and the migration in ONE transaction, starting with `SET LOCAL lock_timeout = '10s'` and `SELECT pg_advisory_xact_lock(<constant>)`; any failure rolls back and propagates out of the constructor without echoing the connection string
    - `INSERT` writes the three new columns; `ON CONFLICT DO UPDATE` still updates only `title`, `image_url`, `claimed_by` (new fields are write-once)
    - `_row_to_item` maps the new columns, giving `None` for legacy rows; `delete_item` uses a parameterised `DELETE ... WHERE board_id = %s AND id = %s` and returns `cursor.rowcount > 0`
    - _Requirements: 13.4, 13.5, 13.6, 13.9, 14.1, 14.3, 14.4_

  - [x] 16.3 Write in-memory store unit tests in `tests/test_store.py`
    - `delete_item` returns `True` then `False`, preserves the order of remaining items, does not affect other boards, and leaves the per-item lock reusable
    - `added_by`, `employee_id`, `bring_date` round-trip through save/get/list; legacy items read back with all three `None`
    - _Requirements: 14.1, 14.3, 14.4_

- [x] 17. Service layer: strict validation, new `add_item`, `delete_item`
  - [x] 17.1 Add validation helpers and the new `add_item` signature in `app/service.py`
    - Add `_require_text(value, label, max_len)` (non-string/missing/blank/too long raise `ValidationError`; messages name the field and never echo the value) and `_require_date` (ASCII regex `[0-9]{4}-[0-9]{2}-[0-9]{2}` via `fullmatch` first, then `date.fromisoformat`; any real date accepted)
    - New signature `add_item(board_id, title, added_by, employee_id, bring_date, image_url=None)`: limits 100/60/30, all four validated and trimmed BEFORE the store is touched (no lazily created board on failure), stores the trimmed values, broadcasts exactly one `ItemAdded`; `claim_item`/`unclaim_item` and their name validation are unchanged and require only the name
    - Update the single call site in the existing `app/api.py` add route to forward the four body values so the app stays runnable; the full request model and error handling follow in 18.1
    - _Requirements: 1.1, 2.6, 3.1, 7.1, 7.3, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.11_

  - [x] 17.2 Implement `delete_item` in `app/service.py`
    - Signature `async delete_item(board_id, item_id, employee_id: str | None) -> Item`; no validation on `employee_id` (it is only compared)
    - Inside the per-item lock, in exactly this order: (1) missing item raises `NotFoundError`; (2) if the stored `employee_id` is set and the submitted id does not match (trim + casefold; `None`/blank/non-string = missing) raise `PermissionError` with ONE constant generic message independent of stored and submitted values; (3) if `claimed_by` is set raise `ConflictError` with `current_claimer` and the "bringer must release it first" message; (4) `store.delete_item`, and raise `NotFoundError` if it returns `False`
    - Legacy items (`employee_id is None`) skip step 2 entirely; compare with `hmac.compare_digest` on the UTF-8 bytes of the normalized ids (`_norm_id`, `_ids_match`)
    - After releasing the lock broadcast exactly one `ItemDeleted` carrying the removed item and return it; no broadcast on any failure; the stored employee id is used for nothing else
    - _Requirements: 3.1, 3.4, 5.4, 11.4, 11.5, 12.7, 12.8, 12.9, 12.10, 12.11, 12.12, 12.16, 13.1, 13.3, 13.8_

- [x] 18. REST API: new add body, delete route, sanitized errors
  - [x] 18.1 Update the add route and validation-error handling in `app/api.py`
    - `AddItemRequest` with loosely typed optional fields (`title, added_by, employee_id, bring_date, image_url: Any = None`) so missing or wrongly typed values reach the service validator and yield 400, not 422; `POST /board/{board_id}/items` calls `service.add_item(board_id, title, added_by, employee_id, bring_date, image_url)` and returns 201 with the public dict
    - Register a `RequestValidationError` handler in `register_exception_handlers` returning a fixed `400 {"detail": "The request was not valid. Please check the fields and try again."}` that never echoes request values (covers malformed JSON, non-object bodies, and claim/unclaim body-shape errors, which now return 400 instead of 422)
    - _Requirements: 2.6, 7.6, 9.2, 10.5, 11.1, 11.5_

  - [x] 18.2 Add the delete route and error mapping in `app/api.py`
    - `DeleteItemRequest` (`employee_id: Any = None`); `POST /board/{board_id}/items/{item_id}/delete` with `body: DeleteItemRequest | None = None` reading the id from the JSON body only (no path or query parameter, so an id supplied there counts as missing)
    - Success returns 200 with `item_to_dict` of the deleted item; map `NotFoundError` to 404, the service `PermissionError` to 403 (constant message), `ConflictError` to 409 with `current_claimer`; confirm no error body contains the submitted or stored id
    - _Requirements: 10.5, 11.1, 11.5, 12.5, 12.6, 12.7, 12.8, 12.9, 12.10, 12.11, 13.3, 13.8_

- [ ] 19. Backend tests: update existing tests and cover the enhancement
  - [x] 19.1 Add a test helper in `tests/conftest.py` and update ALL existing tests to the new signature and 400 behavior
    - Add `add(service, board, title="Dish", *, by="Asha", emp="E1", date="2026-10-12", image=None)` (async-aware or with a `run` wrapper, matching the existing `make_service`/`run` style) and use it in `test_service_broadcasts.py`, `test_claim_exclusivity.py`, `test_service_properties.py` (extend the add op to carry the new fields), `test_pg_store.py`, `test_realtime_integration.py` and `test_api.py`
    - Change `test_api.py` expectations for body-shape errors from 422 to 400 and the add request bodies to the four-field form; keep every existing test green and assert claims still work with only a name
    - _Requirements: 1.1, 2.6, 7.3, 7.6_

  - [ ] 19.2 Write the add-validation matrix in `tests/test_service_validation.py`
    - Each of the four fields missing, `None`, non-string (number, list, dict), blank, whitespace-only, over the limit (100/60/30 pass, 101/61/31 fail); padded values stored trimmed; dates `2026-02-30`, `2026-10-5`, `2026-1-05`, `20261012`, `2026-W41-1` and non-ASCII digits rejected; `2025-01-01` and `2030-12-31` accepted
    - Every rejection leaves the store empty (no lazily created board) and the fake hub with no events; messages never contain the submitted value
    - _Requirements: 7.1, 7.3, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.11_

  - [ ] 19.3 Write property test for add validation in `tests/test_prop_add_validation.py`
    - **Property 9: Add requires four valid fields**
    - Valid strategy near the limits plus an invalid strategy that mutates exactly one field at a time; `st.dates()` formatted with `isoformat()` is accepted, and arbitrary text is accepted only if it equals `date.isoformat()` of some date; on success the stored values are the trimmed inputs, on failure `ValidationError`, unchanged store and no event
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.11**

  - [ ] 19.4 Write the delete authorization matrix in `tests/test_service_delete.py`
    - Cross submitted id (missing, `None`, blank, wrong, right, padded and differently cased, over 30 characters) with state (Available, claimed) with item kind (new, legacy); assert 404 for unknown item or board, 403 before 409 (claimed item with a wrong id gives 403), 409 for claimed with right id and for claimed legacy, success for legacy with any submitted id
    - The 403 message is identical for different stored ids and different submitted ids; a rejected delete leaves the store and fake hub untouched; after a successful delete the item is gone from `get_item`, the snapshot and `list_people`, and deleting again raises `NotFoundError`
    - _Requirements: 5.4, 11.5, 12.7, 12.8, 12.9, 12.10, 12.11, 13.1, 13.2, 13.3, 13.8_

  - [ ] 19.5 Write property test for delete authorization in `tests/test_prop_delete_authorization.py`
    - **Property 11: Delete authorization**
    - For random items (new and legacy, claimed and Available) and submitted ids drawn from `None`, blank, whitespace, non-string, over-length, wrong, and the real id with random case and padding, assert the outcome equals the decision order of Property 11 and the error message is constant
    - **Validates: Requirements 11.5, 12.7, 12.8, 12.9, 12.10, 12.11, 13.3, 13.8**

  - [ ] 19.6 Extend broadcast counting tests in `tests/test_service_broadcasts.py`
    - **Property 6: Realtime fan-out** extended with delete: exactly one `ItemDeleted` for a successful delete, none for 404/403/409 failures, and the event's item id equals the deleted id
    - **Validates: Requirements 3.1, 3.4, 12.12**

  - [ ] 19.7 Extend the Hypothesis op-sequence generator in `tests/test_service_properties.py`
    - **Property 12: Delete removes exactly one item and broadcasts exactly once**
    - Add `("delete", target_index, submitted_id)` ops (submitted id drawn from the real id, real id with different case and padding, wrong id, `None`, blank, over-length) and carry generated `(title, added_by, employee_id, bring_date)` on add ops; update a pure-Python reference model of the Property 11 rules alongside and compare real state, error category and recorded hub events after every step
    - Keep the existing Properties 1, 2, 4, 5, 8 assertions running over the extended generator
    - **Validates: Requirements 3.1, 3.4, 5.4, 12.7, 12.12**

  - [ ] 19.8 Write property test that the people view excludes deleted items in `tests/test_prop_people_excludes_deleted.py`
    - **Property 16: People view excludes deleted items**
    - Reuse the generator from `tests/test_service_properties.py`; after every op the multiset of items in `list_people` equals the set of claimed items that currently exist, no deleted item appears, and a successful delete never changes any person's entry
    - **Validates: Requirements 5.1, 5.4**

  - [ ] 19.9 Extend claim exclusivity tests in `tests/test_claim_exclusivity.py`
    - **Property 13: Claim and delete are mutually exclusive under concurrency**
    - One `claim_item` racing one correct-id `delete_item` via `asyncio.gather`, repeated many times, and a threaded variant using separate event loops; derive the winner from the results and assert exactly one succeeds (claim wins: delete gets `ConflictError`; delete wins: claim gets `NotFoundError`), exactly one of `ItemClaimed`/`ItemDeleted` is broadcast, and the final store state matches the winner
    - Keep the existing Property 3 claim-vs-claim tests passing
    - **Validates: Requirements 12.16**

  - [ ] 19.10 Write property test that the employee id never leaves the server in `tests/test_prop_employee_id_privacy.py`
    - **Property 10: Employee ID never leaves the server**
    - Drive random op sequences (add, claim, unclaim, delete, invalid bodies) with distinctive random ids (`"EMP-" + token`) through `TestClient` with a recording hub; recursively walk every REST response body (snapshot, people, add, claim, unclaim, delete, and 400/403/404/409 error bodies, including a 400 produced from a body containing the id), every broadcast payload and every WebSocket frame for any key named like `employee_id` (any casing) and any string equal to the id (trimmed, case-insensitive), plus a raw-text substring scan
    - **Validates: Requirements 10.5, 10.6, 11.1, 11.2, 11.5**

  - [ ] 19.11 Extend the WebSocket integration test in `tests/test_realtime_integration.py`
    - Two sockets on one board: add via REST then delete via REST; both receive exactly one `ItemDeleted` shaped `{type, board_id, item}` with the matching id and no `employee_id` key or value in any frame; a fresh snapshot lacks the item; failed deletes (403, 409, 404) send no frame; a socket on another board receives nothing
    - _Requirements: 3.1, 3.4, 11.2, 12.12_

  - [ ] 19.12 Write property test for snapshot and event convergence including deletion in `tests/test_convergence.py`
    - **Property 14: Snapshot + events convergence, including deletion**
    - Python port of `applyEvent` (add, claim, unclaim, delete-by-id, no-op when absent, input never mutated); the op generator includes deletes; compare the client result with a fresh snapshot at several cut points, including a replay of buffered events over a snapshot taken after the subscribe ack that already reflects a later deletion
    - **Validates: Requirements 3.3, 12.13**

  - [ ] 19.13 Extend API tests in `tests/test_api.py`
    - Add: 201 with `added_by` and `bring_date` and no `employee_id`; 400 for each invalid field, missing keys, wrong types (`{"title": 5}`), malformed JSON and a non-object body; the 400 body is the fixed sanitized text and contains none of the submitted values
    - Delete: 200 with the public item for the owner (id case and space insensitive); 403 for wrong or missing id, `{}`, no body, id only in the query string or path; 409 for claimed with right id and for claimed legacy; 403 for claimed with wrong id; 404 for unknown item and unknown board with any id; legacy delete with no body, empty, whitespace and a 31+ character id; the 403 bodies for different stored/submitted ids are byte-identical
    - Claim/unclaim still return 200/409/403/404 and 400 (not 422) for body-shape errors; snapshot and people responses include `added_by`/`bring_date` (`null` for legacy items) and no employee id
    - _Requirements: 2.6, 7.5, 7.6, 7.7, 7.8, 9.2, 10.5, 11.1, 11.5, 12.6, 12.7, 12.8, 12.9, 12.10, 12.11, 13.3, 13.8_

- [ ] 20. Checkpoint - backend complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 21. Postgres tests (skipped without `DATABASE_URL`)
  - All Postgres tests keep the existing `pytest.mark.skipif(not os.getenv("DATABASE_URL"))` guard, use unique random board ids, delete only their own rows by board id, and NEVER `TRUNCATE` or `DROP` shared tables.
  - [ ] 21.1 Extend `tests/test_pg_store.py` for the new columns and delete
    - `added_by`, `employee_id`, `bring_date` round-trip unchanged, including from a second `PostgresBoardStore` instance (restart simulation); legacy-style items saved with `None` read back as `None`
    - `delete_item` returns `True` then `False`, leaves other items and other boards untouched, preserves order, and handles quotes in titles and ids (parameterised); claim then unclaim does not overwrite the three write-once columns
    - _Requirements: 13.6, 14.1, 14.3, 14.4_

  - [ ] 21.2 Write the migration test in `tests/test_pg_migration.py`
    - **Property 15: Store parity and migration idempotence** (migration part)
    - Create a uniquely named throwaway schema (for example `test_mig_<random>`), point the connection `search_path` at it, hand-build the OLD `potluck_items` schema (original columns only), insert a legacy row, record row count and column values, then run `ensure_schema` three times; assert the three columns exist exactly once, row count and every original value are unchanged, the legacy row reads back through the store with `added_by`, `employee_id`, `bring_date` all `None`, and a store on the migrated table can add a new-style item and delete the legacy one
    - Real data is never touched: only the throwaway schema is dropped in cleanup, never `public` or the shared tables
    - **Validates: Requirements 13.4, 13.5, 13.6, 14.1**

  - [ ] 21.3 Write the migration failure test in `tests/test_pg_migration_failure.py`
    - **Property 15: Store parity and migration idempotence** (failure part)
    - In a throwaway schema with the old table and a legacy row, force one migration statement to fail (monkeypatch a statement to invalid SQL); assert the exception propagates from the store constructor, the error text does not contain the connection string, and the pre-existing rows and columns are unchanged (transaction rollback, no partial columns)
    - **Validates: Requirements 13.9**

  - [ ] 21.4 Write store parity tests in `tests/test_store_parity.py`
    - **Property 15: Store parity and migration idempotence** (parity part)
    - A shared runner executes a generated add/claim/unclaim/delete op list through `PotluckService` against a store and returns a normalized trace (per op `("ok", fields without id/created_at)` or `("err", category)`, generated ids mapped to creation indexes) plus the final snapshot (items in order, grouped people); the in-memory run always executes, and the `trace(memory) == trace(postgres)` comparison is skipped without `DATABASE_URL`; the Postgres run uses its own unique board id and cleans up by board id only
    - **Validates: Requirements 14.1, 14.2, 14.3, 14.4, 14.5**

- [ ] 22. Checkpoint - Postgres store verified
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 23. Frontend client modules
  - [ ] 23.1 Update `static/js/api.js`
    - `addItem` takes one payload object `{ title, added_by, employee_id, bring_date, image_url }` and sends `image_url: image_url || null`; update its existing caller in `static/js/app.js` minimally so the module graph stays consistent until the form is rebuilt in 24.3
    - Add `deleteItem(itemId, employeeId)` posting `{ employee_id: employeeId ?? "" }` to `/items/{id}/delete` (id URL-encoded, employee id in the JSON body only)
    - `request()` gains optional `timeoutMs` using `AbortController` (abort timer cleared in `finally`); an abort maps to an `ApiError` with status 0 and the message "The request timed out. Please try again.", distinct from the "Can't reach the server" message; `deleteItem` uses 10000 ms
    - _Requirements: 9.10, 12.5, 12.6, 12.15_

  - [ ] 23.2 Update `applyEvent` in `static/js/realtime.js` for `ItemDeleted`
    - Add `case "ItemDeleted": if (idx !== -1) next.splice(idx, 1); break;` so removal is idempotent (no-op when the id is absent); `applyEvent` still returns a new array and never mutates its input
    - Leave `BoardClient` buffering, ordering and reconnect logic unchanged
    - _Requirements: 3.3, 12.13, 12.14_

- [ ] 24. Frontend UI: add form, card meta line, delete confirmation
  - [ ] 24.1 Add the small theme additions to `static/css/theme.css`
    - Append `.item-meta` (with `.meta-label`, `.meta-sep`), `.delete-confirm` (with `.delete-prompt`, `.delete-actions`), `.btn-danger` and `.btn-danger:hover:not(:disabled)` exactly as in the design, reusing existing variables and adding no new colors
    - _Requirements: 10.4, 12.3_

  - [ ] 24.2 Update the add form markup in `static/index.html`
    - Fields in order: Your name, Employee ID (`autocomplete="off"`, `maxlength="30"`), Food item (`maxlength="100"`), Date (`<input type="date" required value="2026-10-12">`), then the optional image link and suggestion chips; each with a visible `<label>`, `required` (except image), and a per-field inline error element (`role="alert"`, linked via `aria-describedby`) plus a form-level error line reusing `.form-error`
    - _Requirements: 9.8, 9.9, 9.10_

  - [ ] 24.3 Implement the add-form logic in `static/js/app.js`
    - Pre-fill Your name from the saved claim name (`potluck.name`) and follow it only until the user edits it (`dirty` flag); client-side checks mirror the server (non-empty, 100/60/30 limits, `YYYY-MM-DD`) with `aria-invalid` and inline errors
    - Submit calls `api.addItem({...})`; on a server error show the message next to the form, retain EVERY entered value and add no card; on success clear only title, image and employee id and keep the chosen date; the Employee ID is never written to `localStorage` or `sessionStorage`
    - _Requirements: 1.1, 9.1, 9.8, 9.9, 9.10, 11.3_

  - [ ] 24.4 Render the Added by and date meta line in `static/js/app.js`
    - Export `formatBringDate(iso)` using manual regex parsing (never `new Date(...)`): `2026-10-05` gives `5 Oct 2026`, invalid or missing gives `null`, no time-zone shift
    - `renderItemCard` adds an `.item-meta` line with a labelled `<span class="meta-label">Added by</span>` element separate from the "Bringing:" line (name shown in full) and a `<time datetime="YYYY-MM-DD">` element; legacy items omit only the missing part with no placeholder or empty label; all user text via `textContent` (`innerHTML` stays unused)
    - _Requirements: 4.3, 10.1, 10.2, 10.3, 10.4, 10.7_

  - [ ] 24.5 Implement the delete button and inline Delete_Confirmation in `static/js/app.js`
    - Available cards show `Delete` (`btn btn-small btn-danger-ghost`, `aria-label="Delete <title>"`); claimed cards show none
    - Delegated `delete` / `cancel-delete` actions and a confirmation `<form>` (so Enter confirms) naming the item, with a labelled Employee ID input, an error line (`role="alert"`) and Confirm delete / Cancel buttons; no request before Confirm and none on Cancel; for a legacy item (no `added_by` and no `bring_date`) show the hint that the Employee ID may be left empty and allow submitting empty
    - Panel state `{itemId, value, error, busy}` lives in one JS variable (not the DOM) so realtime re-renders keep what the user typed; while busy disable Confirm/Cancel and make the input read-only so exactly one request is sent via `api.deleteItem` (10 s timeout); drop the panel with a short message if the item becomes claimed or disappears
    - Focus management: open focuses the input; Cancel/Escape returns focus to that card's Delete button; success moves focus to the items summary (`tabindex="-1"`); errors keep focus in the input; widen `findFocusTarget` to `[data-action]`; add `deleteErrorMessage` (403 generic server text, 409 server message verbatim without "Already taken by X", 404, timeout and network texts) and keep `errorMessage` for claim/unclaim
    - The typed id is held only in that variable and input; render the Employee ID nowhere else
    - _Requirements: 5.4, 11.3, 12.1, 12.2, 12.3, 12.4, 12.5, 12.13, 12.14, 12.15, 13.7_

  - [ ] 24.6 Write a static privacy guard test in `tests/test_static_privacy.py`
    - Scan `static/index.html` and `static/js/*.js` for `innerHTML` (must be absent), for any `localStorage`/`sessionStorage` access whose key or value involves the employee id, and for `employee_id` appearing in rendered text or attributes outside the add-form input, the confirmation input and the `api.js` request bodies
    - _Requirements: 10.7, 11.3_

- [ ] 25. Documentation
  - [ ] 25.1 Update `README.md`
    - Document the new add fields (name, employee ID, food item, date; limits 60/30/100 and strict `YYYY-MM-DD`), the delete rule (Available items only, employee ID in the JSON body, legacy items deletable without an id, claimed items must be released first), the API table additions (`POST /board/{board_id}/items/{item_id}/delete`, changed add body, `ItemDeleted` event, 400 instead of 422 for body-shape errors), a privacy note (employee ID is stored server side only and never returned; it is a lightweight ownership check, not authentication), and the upgrade note (Postgres migrates in place and idempotently on startup; legacy items keep working)
    - Change nothing else (no version bumps)
    - _Requirements: 9.1, 11.1, 12.6, 13.4, 13.5_

- [ ] 26. Final checkpoint - ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
  - Manual smoke checklist for the user (no browser test runner exists): required fields and inline errors; values retained after a 400; date pre-filled `2026-10-12`; card meta line for new and legacy items; `<`, `>`, `&` in names rendered literally; Delete shown on Available cards only; confirmation flow (no request before Confirm, Cancel sends nothing, legacy hint, focus handling, 10 s timeout by throttling); live card removal in a second browser tab; People view update; employee ID absent from the DOM, `localStorage` and network responses.

## Notes

- Tasks marked with `*` are optional (tests) and can be skipped for a faster MVP, but they validate the 8 Correctness Properties and realtime behavior and are recommended.
- Each task references specific requirement sub-clauses for traceability.
- Checkpoints ensure incremental validation after each major layer.
- Property tests use Hypothesis over random add/claim/unclaim operation sequences; the claim-exclusivity test simulates concurrent claims.
- The application is delivered as complete working code (backend + frontend), not stubs.
- Enhancement tasks 15 to 26 have no optional (`*`) sub-tasks: the `add_item` signature change breaks existing tests, so they are updated in the same sweep (19.1) and all new tests are required. Properties 9 to 16 from the design are each covered by a named property test sub-task.
- Postgres tests never touch real data: unique board ids, cleanup by board id only, no `TRUNCATE`/`DROP` on shared tables, and the migration tests use a uniquely named throwaway schema.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1"] },
    { "id": 2, "tasks": ["2.2", "3.2", "4.1"] },
    { "id": 3, "tasks": ["4.2", "4.3"] },
    { "id": 4, "tasks": ["4.4", "4.5", "4.6"] },
    { "id": 5, "tasks": ["4.7", "4.8"] },
    { "id": 6, "tasks": ["4.9", "4.10", "6.1"] },
    { "id": 7, "tasks": ["6.2", "6.3"] },
    { "id": 8, "tasks": ["7.1"] },
    { "id": 9, "tasks": ["9.1", "9.2"] },
    { "id": 10, "tasks": ["10.1", "10.2", "10.3", "10.4"] },
    { "id": 11, "tasks": ["12.1", "13.1"] },
    { "id": 12, "tasks": ["15.1", "16.1"] },
    { "id": 13, "tasks": ["15.2", "16.2", "17.1"] },
    { "id": 14, "tasks": ["15.3", "16.3", "17.2", "18.1"] },
    { "id": 15, "tasks": ["18.2", "19.1"] },
    { "id": 16, "tasks": ["19.2", "19.3", "19.4", "19.5", "19.6", "19.7", "19.9", "19.10", "19.11", "19.12", "19.13"] },
    { "id": 17, "tasks": ["19.8", "21.1", "21.2", "21.3", "21.4"] },
    { "id": 18, "tasks": ["23.1", "23.2", "24.1", "24.2"] },
    { "id": 19, "tasks": ["24.3"] },
    { "id": 20, "tasks": ["24.4"] },
    { "id": 21, "tasks": ["24.5"] },
    { "id": 22, "tasks": ["24.6", "25.1"] }
  ]
}
```
