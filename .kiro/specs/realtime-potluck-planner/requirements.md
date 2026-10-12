# Requirements Document

## Introduction

The Realtime Potluck Planner is a web application that lets office colleagues coordinate a potluck by adding food items, claiming who brings what, and viewing a live, shared status of every item. It eliminates duplication and gaps by exposing a single, link-shareable board that updates in realtime for everyone viewing it.

The system is organized around **Items** (a dish to bring) and **Participants** (self-declared named people). An item is either **Available** (unclaimed) or **Already Taken** (claimed by exactly one participant, shown as "Bringing: <name>"). Mutations fan out to all connected clients in realtime, with a REST snapshot for initial load and reconnect convergence.

By explicit product decision, the system uses **no authentication**: anyone with the shared office link joins the same board, and identity is a self-declared display name. The user interface follows the **PIRNAV "Potluck Week 2026"** festive theme documented in the design's "Visual Theme & Branding" section.

These requirements are derived from the approved design document (`design.md`). The requirement numbering is aligned with the "Validates: Requirements X.Y" references in the design's Correctness Properties section.

## Glossary

- **Potluck_Planner**: The overall web application system comprising the service layer, REST API, WebSocket hub, store, and web UI.
- **Potluck_Service**: The backend business-logic component that validates and applies mutations, computes derived state, and emits change events.
- **Status_Board**: The UI view that renders every item as a card with its title, optional image, bringer label, and status badge.
- **People_View**: The UI view (tab) that groups claimed items by the participant bringing them.
- **Realtime_Service**: The WebSocket hub and broadcaster that pushes change events to all clients subscribed to a board.
- **REST_API**: The HTTP interface used for initial state snapshots and for mutation requests.
- **Validator**: The server-side input-validation logic that trims and checks titles and names.
- **Board**: One shared potlucking context identified by an unguessable board id embedded in the shareable link.
- **Item**: A dish to bring, with a unique id, title, optional image, and an optional claimer name.
- **Participant**: A person identified by a self-declared, non-empty display name.
- **Available**: The derived status of an Item whose claimer name is unset.
- **Already_Taken**: The derived status of an Item whose claimer name is set to a non-empty name.
- **Change_Event**: A realtime payload (`ItemAdded`, `ItemClaimed`, or `ItemUnclaimed`) describing a single successful mutation.
- **Board_Snapshot**: The full current state of a board (items and grouped people) returned by the REST state endpoint.

## Requirements

### Requirement 1: Add Food Items

**User Story:** As a colleague, I want to add a food item to the board with an optional image, so that others can see what dishes are planned and choose what to bring.

#### Acceptance Criteria

1. WHEN a Participant submits a food item with a title whose trimmed value is non-empty, THE Potluck_Service SHALL create exactly one new Item with a unique id, an unset claimer, and derived status Available, without modifying any existing Item.
2. WHEN a new Item is created, THE Potluck_Service SHALL store the Item so that it is retrievable in the Board_Snapshot.
3. WHERE a Participant provides an image reference when adding a food item, THE Potluck_Service SHALL associate that image reference with the created Item.
4. WHEN a new Item is created, THE Status_Board SHALL display the Item with the bringer label "Bringing: Not assigned".

### Requirement 2: Claim and Release Items

**User Story:** As a colleague, I want to claim an available item by adding my name and later release it, so that I can commit to bringing a dish and change my mind without creating duplicates.

#### Acceptance Criteria

1. WHEN a Participant claims an Available Item with a name whose trimmed value is non-empty, THE Potluck_Service SHALL set the Item claimer to the trimmed name, set the derived status to Already_Taken, and record exactly one claimer for that Item.
2. IF a Participant claims an Item that is already Already_Taken, THEN THE Potluck_Service SHALL reject the claim with a conflict response that includes the current claimer name and SHALL leave the Item state unchanged.
3. WHEN a Participant whose name matches the current claimer releases an Already_Taken Item, THE Potluck_Service SHALL unset the Item claimer and set the derived status to Available.
4. IF a Participant whose name does not match the current claimer attempts to release an Item, THEN THE Potluck_Service SHALL reject the release with a forbidden response and SHALL leave the Item state unchanged.
5. WHEN an Item becomes Already_Taken, THE Status_Board SHALL display the bringer label "Bringing: <name>" using the claimer name.

### Requirement 3: Realtime Updates and Reconnect Convergence

**User Story:** As a colleague viewing the board, I want changes made by anyone to appear live, so that I always see the current state without manually refreshing.

#### Acceptance Criteria

1. WHEN a mutation (add, claim, or release) succeeds, THE Realtime_Service SHALL broadcast exactly one Change_Event describing that mutation to every client currently subscribed to the affected Board.
2. WHEN a client loads a Board, THE REST_API SHALL return a Board_Snapshot of the current items and grouped people before the client subscribes to live updates.
3. WHEN a client applies a Board_Snapshot and then applies every subsequent Change_Event for that Board, THE Potluck_Planner SHALL cause the client state to equal the state of a client that loads a fresh Board_Snapshot at the same later time.
4. IF a mutation fails validation, conflict, forbidden, or not-found checks, THEN THE Realtime_Service SHALL broadcast no Change_Event for that mutation.
5. IF a client's realtime connection drops, THEN THE Potluck_Planner SHALL re-fetch the Board_Snapshot and resubscribe upon reconnection.

### Requirement 4: Live Item Status Board

**User Story:** As a colleague, I want a shared board showing every item and whether it is still available, so that I can quickly see what is covered and what still needs a bringer.

#### Acceptance Criteria

1. THE Potluck_Service SHALL derive each Item status as Already_Taken when the Item claimer is a non-empty name and as Available otherwise.
2. WHEN rendering an Item, THE Status_Board SHALL display a green "Already Taken" badge for an Already_Taken Item and a grey "Available" badge for an Available Item.
3. WHEN rendering the Board, THE Status_Board SHALL display every Item on the Board as a card showing its title, its image when present, its bringer label, and its status badge.

### Requirement 5: People View

**User Story:** As a colleague, I want to see which person is bringing which items, so that I can coordinate contributions and recognize who has committed.

#### Acceptance Criteria

1. WHEN building the People_View, THE Potluck_Service SHALL produce exactly one entry per distinct claimer name such that the collection of items across all entries equals the set of claimed items on the Board and no unclaimed Item appears in any entry.
2. WHERE two claimer names differ only by letter casing, THE Potluck_Service SHALL group their items under a single entry while preserving the original casing for display.
3. WHEN displaying the People_View, THE People_View SHALL list each Participant together with the item or items that Participant is bringing.

### Requirement 6: Shareable Board Link Without Authentication

**User Story:** As the office organizer, I want a single shareable link that anyone can open without signing in, so that colleagues can join the same board with no friction.

#### Acceptance Criteria

1. THE Potluck_Planner SHALL address each Board by a URL that embeds an unguessable board id.
2. WHEN a Participant opens a Board URL, THE Potluck_Planner SHALL grant access to view and mutate that Board without requiring authentication.
3. THE Status_Board SHALL provide a copyable Board link that a Participant can share with the office.
4. WHEN two or more clients open the same Board URL, THE Potluck_Planner SHALL present them the same shared Board state.

### Requirement 7: Input Validation and Error Handling

**User Story:** As a colleague, I want the system to reject invalid input and report clear errors, so that the board stays accurate and I understand what went wrong.

#### Acceptance Criteria

1. IF a Participant submits an item title whose trimmed value is empty, THEN THE Validator SHALL reject the request with a validation error and SHALL make no state change and trigger no broadcast.
2. IF a Participant submits a claim or release name whose trimmed value is empty, THEN THE Validator SHALL reject the request with a validation error and SHALL make no state change and trigger no broadcast.
3. WHEN the Validator accepts a title or name, THE Validator SHALL store the trimmed value.
4. IF a request references a non-existent Item or Board, THEN THE Potluck_Service SHALL reject the request with a not-found error and SHALL make no state change.
5. IF a claim targets an Already_Taken Item, THEN THE REST_API SHALL respond with a 409 Conflict status.
6. IF a request fails title or name validation, THEN THE REST_API SHALL respond with a 400 Bad Request status.
7. IF a request references a non-existent Item or Board, THEN THE REST_API SHALL respond with a 404 Not Found status.
8. IF a Participant attempts to release an Item claimed by a different name, THEN THE REST_API SHALL respond with a 403 Forbidden status.

### Requirement 8: Festive Theme and Branding

**User Story:** As a colleague, I want the board styled with the PIRNAV Potluck Week 2026 festive theme, so that the experience feels celebratory and on-brand.

#### Acceptance Criteria

1. THE Potluck_Planner SHALL display the PIRNAV Software Solutions brand identity and the "Potluck Week 2026" event title with the tagline "A Celebration of Food, Culture, Tradition & Togetherness".
2. THE Potluck_Planner SHALL display the event dates 12th–16th October 2026 as an event banner or badge.
3. THE Potluck_Planner SHALL display the closing tagline "Come together, share a little, celebrate a lot!".
4. THE Potluck_Planner SHALL apply the warm festive color palette defined in the design, using marigold orange for primary actions, deep maroon for banners and headers, warm cream for the page background, the accent green for Already_Taken success states, and grey for Available states.
5. THE Status_Board SHALL render the Already_Taken status badge in the accent green and the Available status badge in grey.
6. THE Potluck_Planner SHALL present a landing view with four feature tiles labeled Add Food Items, Add Your Name, Live Item Status, and Share with Office, styled with the category motifs Traditional Attire, Potluck, Fun Activities, and Team Games.
