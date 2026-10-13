# Requirements Document

## Introduction

The Realtime Potluck Planner is a web application that lets office colleagues coordinate a potluck by adding food items, claiming who brings what, and viewing a live, shared status of every item. It eliminates duplication and gaps by exposing a single, link-shareable board that updates in realtime for everyone viewing it.

The system is organized around **Items** (a dish to bring) and **Participants** (self-declared named people). An item is either **Available** (unclaimed) or **Already Taken** (claimed by exactly one participant, shown as "Bringing: <name>"). Mutations fan out to all connected clients in realtime, with a REST snapshot for initial load and reconnect convergence.

By explicit product decision, the system uses **no authentication**: anyone with the shared office link joins the same board, and identity is a self-declared display name. The user interface follows the **PIRNAV "Potluck Week 2026"** festive theme documented in the design's "Visual Theme & Branding" section.

**Enhancement (Requirements 9 to 14):** Adding an Item now captures four mandatory values: the adder's name, the adder's employee ID, the food item title, and the date the adder will bring the Item. The adder's name and the date are shown on the Item card. The employee ID is kept server side only and is used as a lightweight ownership check so that the adder can delete an Item that is still Available. The employee ID is not authentication: the board link stays public with no sign-in, and the employee ID is never shown in the UI or returned in any API response or realtime event. Claiming ("I'll bring this") is unchanged. Items created before this enhancement (Legacy_Items) keep working, and the persistent store migrates in place without data loss.

These requirements are derived from the approved design document (`design.md`). The requirement numbering is aligned with the "Validates: Requirements X.Y" references in the design's Correctness Properties section. Requirements 1 to 8 retain their original numbering; amended criteria keep their original numbers and new criteria and requirements are appended.

## Glossary

- **Potluck_Planner**: The overall web application system comprising the service layer, REST API, WebSocket hub, store, and web UI.
- **Potluck_Service**: The backend business-logic component that validates and applies mutations, computes derived state, and emits change events.
- **Status_Board**: The UI view that renders every item as a card with its title, optional image, bringer label, and status badge.
- **People_View**: The UI view (tab) that groups claimed items by the participant bringing them.
- **Realtime_Service**: The WebSocket hub and broadcaster that pushes change events to all clients subscribed to a board.
- **REST_API**: The HTTP interface used for initial state snapshots and for mutation requests.
- **Validator**: The server-side input-validation logic that trims and checks titles, names, Employee_IDs, and Bring_Dates.
- **Board**: One shared potlucking context identified by an unguessable board id embedded in the shareable link.
- **Item**: A dish to bring, with a unique id, title, optional image, an optional claimer name, and, for Items created after the enhancement, an Added_By name, an Employee_ID, and a Bring_Date.
- **Participant**: A person identified by a self-declared, non-empty display name.
- **Available**: The derived status of an Item whose claimer name is unset.
- **Already_Taken**: The derived status of an Item whose claimer name is set to a non-empty name.
- **Change_Event**: A realtime payload (`ItemAdded`, `ItemClaimed`, `ItemUnclaimed`, or `ItemDeleted`) describing a single successful mutation.
- **Board_Snapshot**: The full current state of a board (items and grouped people) returned by the REST state endpoint.
- **Added_By**: The trimmed display name, at most 60 characters, of the Participant who added an Item. It is distinct from the claimer name and is displayed on the Item card.
- **Employee_ID**: The trimmed identifier, non-empty and at most 30 characters, entered by the adder when adding an Item. It is stored server side only, compared after trimming and case-insensitively, and used solely to authorize deletion. It is never displayed and never included in any API response or Change_Event.
- **Bring_Date**: The calendar date, in ISO format `YYYY-MM-DD`, on which the adder will bring the Item. It is not restricted to the Potluck Week range of 12th to 16th October 2026.
- **Item_Deleted_Event**: The `ItemDeleted` Change_Event broadcast after an Item is successfully deleted. It identifies the deleted Item by its id.
- **Delete_Confirmation**: The explicit UI step shown before a delete request is sent, which identifies the Item and collects the Employee_ID.
- **Legacy_Item**: An Item created before the enhancement, which has no stored Added_By, Employee_ID, or Bring_Date.
- **In_Memory_Store**: The non-persistent BoardStore implementation.
- **Postgres_Store**: The persistent, Postgres-backed BoardStore implementation.

## Requirements

### Requirement 1: Add Food Items

**User Story:** As a colleague, I want to add a food item to the board with an optional image, so that others can see what dishes are planned and choose what to bring.

#### Acceptance Criteria

1. WHEN a Participant submits a food item with non-empty trimmed Added_By, Employee_ID, and title values and a valid Bring_Date (Requirement 9), THE Potluck_Service SHALL create exactly one new Item with a unique id, an unset claimer, and derived status Available, without modifying any existing Item.
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
6. WHEN a Participant claims an Available Item, THE Potluck_Service SHALL require only the Participant's name and SHALL NOT require an Employee_ID or a Bring_Date.

### Requirement 3: Realtime Updates and Reconnect Convergence

**User Story:** As a colleague viewing the board, I want changes made by anyone to appear live, so that I always see the current state without manually refreshing.

#### Acceptance Criteria

1. WHEN a mutation (add, claim, release, or delete) succeeds, THE Realtime_Service SHALL broadcast exactly one Change_Event describing that mutation to every client currently subscribed to the affected Board.
2. WHEN a client loads a Board, THE REST_API SHALL return a Board_Snapshot of the current items and grouped people before the client subscribes to live updates.
3. WHEN a client applies a Board_Snapshot and then applies every subsequent Change_Event for that Board, THE Potluck_Planner SHALL cause the client state to equal the state of a client that loads a fresh Board_Snapshot at the same later time.
4. IF a mutation fails validation, conflict, forbidden, or not-found checks, THEN THE Realtime_Service SHALL broadcast no Change_Event for that mutation.
5. IF a client's realtime connection drops, THEN THE Potluck_Planner SHALL re-fetch the Board_Snapshot and resubscribe upon reconnection.

### Requirement 4: Live Item Status Board

**User Story:** As a colleague, I want a shared board showing every item and whether it is still available, so that I can quickly see what is covered and what still needs a bringer.

#### Acceptance Criteria

1. THE Potluck_Service SHALL derive each Item status as Already_Taken when the Item claimer is a non-empty name and as Available otherwise.
2. WHEN rendering an Item, THE Status_Board SHALL display a green "Already Taken" badge for an Already_Taken Item and a grey "Available" badge for an Available Item.
3. WHEN rendering the Board, THE Status_Board SHALL display every Item on the Board as a card showing its title, its image when present, its bringer label, its status badge, and the Added_By name and Bring_Date as specified in Requirement 10.

### Requirement 5: People View

**User Story:** As a colleague, I want to see which person is bringing which items, so that I can coordinate contributions and recognize who has committed.

#### Acceptance Criteria

1. WHEN building the People_View, THE Potluck_Service SHALL produce exactly one entry per distinct claimer name such that the collection of items across all entries equals the set of claimed items on the Board and no unclaimed Item appears in any entry.
2. WHERE two claimer names differ only by letter casing, THE Potluck_Service SHALL group their items under a single entry while preserving the original casing for display.
3. WHEN displaying the People_View, THE People_View SHALL list each Participant together with the item or items that Participant is bringing.
4. WHEN an Item is deleted, THE People_View SHALL exclude that Item from the entries it displays and THE Potluck_Service SHALL exclude that Item from the People_View it produces.

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
3. WHEN the Validator accepts a title, name, Added_By, or Employee_ID, THE Validator SHALL store the trimmed value.
4. IF a request references a non-existent Item or Board, THEN THE Potluck_Service SHALL reject the request with a not-found error and SHALL make no state change.
5. IF a claim targets an Already_Taken Item, THEN THE REST_API SHALL respond with a 409 Conflict status.
6. IF a request fails title, name, Added_By, Employee_ID, or Bring_Date validation, THEN THE REST_API SHALL respond with a 400 Bad Request status.
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

### Requirement 9: Mandatory Adder Details When Adding Items

**User Story:** As a colleague, I want to enter my name, employee ID, the food item, and the date I will bring it when adding an item, so that the board shows who added what and for which day, and so that I can later remove my own item.

#### Acceptance Criteria

1. WHEN a Participant submits an add-item request, THE Validator SHALL require four values: Added_By, Employee_ID, title, and Bring_Date.
2. IF any of Added_By, Employee_ID, title, or Bring_Date is missing, is not a text value, or is empty after removing leading and trailing whitespace, THEN THE Validator SHALL reject the request with a validation error, THE REST_API SHALL respond with a 400 Bad Request status, and THE Potluck_Service SHALL make no state change and trigger no broadcast.
3. IF the trimmed Added_By value is longer than 60 characters, THEN THE Validator SHALL reject the request with a validation error, THE REST_API SHALL respond with a 400 Bad Request status, and THE Potluck_Service SHALL make no state change and trigger no broadcast.
4. IF the trimmed Employee_ID value is longer than 30 characters, THEN THE Validator SHALL reject the request with a validation error, THE REST_API SHALL respond with a 400 Bad Request status, and THE Potluck_Service SHALL make no state change and trigger no broadcast.
5. IF the trimmed Bring_Date value is not a real calendar date written as a four-digit year, two-digit month, and two-digit day separated by hyphens in the format `YYYY-MM-DD` (for example, `2026-02-30` and `2026-10-5` are both invalid), THEN THE Validator SHALL reject the request with a validation error, THE REST_API SHALL respond with a 400 Bad Request status, and THE Potluck_Service SHALL make no state change and trigger no broadcast.
6. WHEN the Validator accepts an add-item request, THE Potluck_Service SHALL store the trimmed Added_By, the trimmed Employee_ID, the trimmed title, and the trimmed Bring_Date in `YYYY-MM-DD` format on the created Item.
7. IF the Bring_Date is a valid calendar date, THEN THE Validator SHALL accept the Bring_Date regardless of whether it falls inside the Potluck Week range of 12th to 16th October 2026.
8. THE Status_Board SHALL present an add-item form containing inputs for Added_By, Employee_ID, title, and Bring_Date, each marked as required.
9. WHEN the add-item form is first displayed, THE Status_Board SHALL pre-fill the Bring_Date input with 2026-10-12 and allow the Participant to change it.
10. IF the REST_API rejects an add-item request with a 400 Bad Request status, THEN THE Status_Board SHALL display the validation message next to the add-item form, SHALL retain the values the Participant entered in the form inputs, and SHALL add no Item card.
11. IF the trimmed title value is longer than 100 characters, THEN THE Validator SHALL reject the request with a validation error, THE REST_API SHALL respond with a 400 Bad Request status, and THE Potluck_Service SHALL make no state change and trigger no broadcast.

### Requirement 10: Display of Added-By and Bring Date

**User Story:** As a colleague, I want each item card to show who added it and on which day it will be brought, so that I can plan around the right day and person.

#### Acceptance Criteria

1. WHEN rendering an Item that has a non-empty Added_By value and a Bring_Date, THE Status_Board SHALL display the Added_By name in full (up to 60 characters, without truncation) and the Bring_Date on the Item card in addition to the title, bringer label, and status badge.
2. IF an Item (including a Legacy_Item) has no Added_By value, no Bring_Date, or neither, THEN THE Status_Board SHALL omit only the missing part from the Item card, SHALL render the title, image when present, bringer label, status badge, and any present part unchanged, and SHALL NOT display placeholder text, an empty label, or an error for the missing part.
3. THE Status_Board SHALL display the Bring_Date in the format `D Mon YYYY`, where `D` is the day of month without a leading zero (1 to 31), `Mon` is the three-letter English month abbreviation with an initial capital (for example, Oct), and `YYYY` is the four-digit year (for example, 12 Oct 2026 and 5 Oct 2026), using the stored calendar date, without shifting the date by the viewer's time zone.
4. THE Status_Board SHALL display the Added_By name in its own labelled element on the Item card, with the label "Added by", separate from the bringer label "Bringing: <name>", so that the adder and the claimer of an Item are distinguishable, including when the Added_By name and the claimer name are identical or when the Item is Available and has no bringer label.
5. THE REST_API SHALL include the Added_By value and the Bring_Date value (in `YYYY-MM-DD` format) for each Item that has them in every Board_Snapshot and every item response, SHALL indicate those values as absent for a Legacy_Item, and SHALL NOT include the Employee_ID.
6. WHEN THE Realtime_Service broadcasts a Change_Event that carries an Item (`ItemAdded`, `ItemClaimed`, or `ItemUnclaimed`), THE Realtime_Service SHALL include the Added_By value and the Bring_Date value (in `YYYY-MM-DD` format) of that Item, SHALL indicate those values as absent for a Legacy_Item, and SHALL NOT include the Employee_ID.
7. WHEN rendering the Added_By name on an Item card, THE Status_Board SHALL display the value as literal text, so that characters such as `<`, `>`, and `&` in the name appear as typed and are not interpreted as markup.

### Requirement 11: Employee ID Confidentiality

**User Story:** As a colleague, I want my employee ID to stay private, so that nobody who opens the public board link can see it or misuse it.

#### Acceptance Criteria

1. THE REST_API SHALL exclude the Employee_ID of every Item, both the stored value and any submitted Employee_ID value, from every response body, including the Board_Snapshot, the People_View response, the add, claim, release, and delete responses, and every error response (including validation, forbidden, not-found, and conflict responses), and SHALL contain no field, in any casing or under any name, whose value equals the Employee_ID under trimmed, case-insensitive comparison.
2. THE Realtime_Service SHALL exclude the Employee_ID of every Item from every Change_Event payload (`ItemAdded`, `ItemClaimed`, `ItemUnclaimed`, and `ItemDeleted`) and from every other message sent to a subscribed client, and SHALL contain no field whose value equals the Employee_ID under trimmed, case-insensitive comparison.
3. THE Status_Board and THE People_View SHALL render no Employee_ID value in visible text, in hidden elements, or in element attributes. The only element that may hold an Employee_ID value is the Delete_Confirmation input, and it may hold only the value the Participant typed during the current confirmation.
4. THE Potluck_Service SHALL use the stored Employee_ID solely to authorize the deletion of an Item, by trimmed, case-insensitive comparison with the submitted Employee_ID. It SHALL NOT use the stored Employee_ID for any other purpose, including adding, claiming, releasing, grouping, ordering, or any outgoing payload.
5. IF a delete request is rejected because the submitted Employee_ID does not match, THEN THE REST_API SHALL return an error response that contains neither the stored Employee_ID nor any portion of it (including any prefix, suffix, masked form, or character count), SHALL return error content that does not vary with the stored Employee_ID value, SHALL leave the Item unchanged, and THE Realtime_Service SHALL broadcast no Change_Event for that request.

### Requirement 12: Delete Available Items

**User Story:** As a colleague, I want to delete an item that I added and that nobody has claimed yet, so that I can remove mistakes or dishes that are no longer needed.

#### Acceptance Criteria

1. WHILE an Item is Available, THE Status_Board SHALL present a Delete action on the card of that Item.
2. WHILE an Item is Already_Taken, THE Status_Board SHALL present no Delete action on the card of that Item.
3. WHEN a Participant activates the Delete action, THE Status_Board SHALL display a Delete_Confirmation that names the Item title and collects the Employee_ID, and SHALL send no request before the Participant confirms.
4. WHEN a Participant cancels the Delete_Confirmation, THE Status_Board SHALL send no request and SHALL leave the Item on the Board.
5. WHEN a Participant confirms the Delete_Confirmation, THE Status_Board SHALL send exactly one delete request that carries the entered Employee_ID in the JSON request body.
6. THE REST_API SHALL read the Employee_ID of a delete request from the JSON request body only, SHALL NOT accept it in the URL path or query string, and SHALL treat an Employee_ID supplied only in the URL path or query string as missing.
7. WHEN a delete request targets an Available Item and the submitted Employee_ID equals the stored Employee_ID after trimming both values and ignoring letter casing, THE Potluck_Service SHALL remove exactly that Item from the Board and SHALL leave every other Item unchanged, and THE REST_API SHALL respond with a success response.
8. IF a delete request targets an Available Item whose stored Employee_ID is set and the submitted Employee_ID is missing, empty, or does not equal the stored Employee_ID after trimming and ignoring letter casing, THEN THE REST_API SHALL respond with a 403 Forbidden status and THE Potluck_Service SHALL leave the Item and the Board unchanged.
9. IF a delete request targets an Already_Taken Item and the submitted Employee_ID matches the stored Employee_ID after trimming and ignoring letter casing, or the Item has no stored Employee_ID, THEN THE REST_API SHALL respond with a 409 Conflict status and a message stating that the bringer must release the Item first, and THE Potluck_Service SHALL leave the Item and the Board unchanged.
10. IF a delete request targets an Already_Taken Item and the Item has a stored Employee_ID that the submitted Employee_ID does not match after trimming and ignoring letter casing, or the submitted Employee_ID is missing or empty, THEN THE REST_API SHALL respond with a 403 Forbidden status and THE Potluck_Service SHALL leave the Item and the Board unchanged.
11. IF a delete request references a non-existent Item or Board, THEN THE REST_API SHALL respond with a 404 Not Found status regardless of the submitted Employee_ID, and THE Potluck_Service SHALL leave the state unchanged.
12. WHEN an Item is successfully deleted, THE Realtime_Service SHALL broadcast exactly one Item_Deleted_Event that identifies the deleted Item by its id to every client currently subscribed to the affected Board within 2 seconds of the deletion.
13. WHEN a client receives an Item_Deleted_Event, THE Status_Board SHALL remove the Item with the identified id from the client state, and SHALL leave the client state unchanged if no such Item is present.
14. WHEN an Item is deleted, THE Status_Board SHALL update the displayed Item cards and any displayed Item counts to exclude the deleted Item without a page reload, and THE People_View SHALL update as specified in Requirement 5.4.
15. IF the REST_API rejects a delete request, or the delete request fails to complete because of a network error or receives no response within 10 seconds, THEN THE Status_Board SHALL display an error message indicating the cause of the failure and SHALL keep the Item card on the Board.
16. IF a claim request and a delete request race on the same Available Item, THEN THE Potluck_Service SHALL apply exactly one of them, so that either the claim succeeds and the delete is rejected with a 409 Conflict status, or the delete succeeds and the claim is rejected with a 404 Not Found status.

### Requirement 13: Legacy Item Compatibility and In-Place Migration

**User Story:** As the office organizer, I want items that were added before this change to keep working, so that no existing board data is lost or broken by the upgrade.

#### Acceptance Criteria

1. THE Potluck_Service SHALL treat an Item whose stored Employee_ID is unset (absent or null) as a Legacy_Item.
2. WHILE an Item is a Legacy_Item, THE Potluck_Service SHALL support claiming, releasing, and listing that Item with the same behavior as for any other Item, such that: a claim by a Participant name, without an Employee_ID or a Bring_Date, on an Available Legacy_Item sets the claimer to the trimmed name and derives status Already_Taken; a release with a matching name unsets the claimer and derives status Available; a claim on an Already_Taken Legacy_Item is rejected with 409 Conflict; a release with a non-matching name is rejected with 403 Forbidden; and the Legacy_Item appears exactly once in every Board_Snapshot and People_View of its Board.
3. WHEN a delete request targets an Available Legacy_Item, THE Potluck_Service SHALL remove exactly that Item and leave every other Item unchanged, THE Realtime_Service SHALL broadcast exactly one Item_Deleted_Event identifying the Item by its id, and THE Potluck_Service SHALL do so whether the request omits the Employee_ID or carries an Employee_ID of any value, including an empty value, a whitespace-only value, or a value longer than 30 characters.
4. WHEN the Postgres_Store initializes, THE Postgres_Store SHALL add the Added_By, Employee_ID, and Bring_Date columns to the Item storage when they are absent, and SHALL preserve every existing Item row and every existing column value, such that the number of Item rows and the value of each pre-existing column in each row are identical before and after initialization.
5. WHEN the Postgres_Store initializes against a database on which it has already initialized successfully one or more times, THE Postgres_Store SHALL complete without raising an error, SHALL NOT create a duplicate column, and SHALL leave the stored data (row count and every column value of every row) identical to its state after the first successful initialization.
6. WHEN the Postgres_Store has migrated an existing Item row, THE Postgres_Store SHALL return that Item with Added_By, Employee_ID, and Bring_Date unset, and with its id, title, image reference, and claimer name equal to their pre-migration values.
7. WHEN the Status_Board renders the Delete_Confirmation for a Legacy_Item, THE Status_Board SHALL display, adjacent to the Employee_ID entry, a visible text label stating that the Employee_ID may be left empty for Items added before the Employee_ID was introduced, and SHALL allow the Delete_Confirmation to be submitted with that entry empty. The Status_Board SHALL identify a Legacy_Item as an Item whose Added_By and Bring_Date are unset in the Board_Snapshot.
8. IF a delete request targets an Already_Taken Legacy_Item, THEN THE REST_API SHALL respond with a 409 Conflict status and a message stating that the bringer must release the Item first, and THE Potluck_Service SHALL leave the Item and the Board unchanged and broadcast no Item_Deleted_Event.
9. IF the Postgres_Store fails to add any of the Added_By, Employee_ID, or Bring_Date columns during initialization, THEN THE Postgres_Store SHALL report the initialization as failed with an error, and SHALL leave every existing Item row and every existing column value unchanged.

### Requirement 14: Storage Backend Parity

**User Story:** As a developer, I want the in-memory and Postgres stores to behave identically, so that tests on one backend give confidence about the other.

#### Acceptance Criteria

1. THE In_Memory_Store and THE Postgres_Store SHALL each persist the Added_By, Employee_ID, and Bring_Date of every Item exactly as accepted by the Validator (trimmed values, Bring_Date in `YYYY-MM-DD` format), SHALL each return those three values unchanged on every subsequent read of that Item for the lifetime of the store (for the Postgres_Store, including after a service restart), SHALL each leave these three values unset for a Legacy_Item, and SHALL each support deleting an Item.
2. WHEN the same sequence of add, claim, release, and delete operations is applied to the In_Memory_Store and to the Postgres_Store starting from the same state, THE Potluck_Service SHALL produce for both stores the same returned Item fields (ignoring generated Item ids), the same error category for each failed operation (validation, not-found, conflict, or forbidden), and a Board_Snapshot with the same Items in the same order, with the same title, image, claimer, derived status, Added_By, and Bring_Date for each Item and the same grouped people.
3. WHEN an Item is deleted from either store, THE store SHALL omit that Item from every subsequent item lookup and item listing, SHALL leave every other Item on the Board unmodified, and SHALL remove the deleted Item's claimer from the grouped people of the Board_Snapshot.
4. IF a delete operation targets an Item id that does not exist or was already deleted, THEN THE In_Memory_Store and THE Postgres_Store SHALL each report the not-found error category and SHALL make no state change.
5. IF a delete operation supplies an Employee_ID that does not match the Item's stored Employee_ID after trimming and case-insensitive comparison, THEN THE In_Memory_Store and THE Postgres_Store SHALL each report the forbidden error category and SHALL leave the Item retrievable with all its values unchanged.
