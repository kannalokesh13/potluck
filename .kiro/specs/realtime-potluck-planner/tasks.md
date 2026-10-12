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

## Notes

- Tasks marked with `*` are optional (tests) and can be skipped for a faster MVP, but they validate the 8 Correctness Properties and realtime behavior and are recommended.
- Each task references specific requirement sub-clauses for traceability.
- Checkpoints ensure incremental validation after each major layer.
- Property tests use Hypothesis over random add/claim/unclaim operation sequences; the claim-exclusivity test simulates concurrent claims.
- The application is delivered as complete working code (backend + frontend), not stubs.

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
    { "id": 11, "tasks": ["12.1", "13.1"] }
  ]
}
```
