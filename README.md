# Realtime Potluck Planner

A small realtime planner for PIRNAV Potluck Week 2026. Add dishes, claim what you are bringing, and watch the Available / Already Taken status update live for everyone on the board. A People tab shows who is bringing what, and a shareable link lets colleagues join the same board.

There is no sign-in, by product decision. Anyone with a board link can use that board.

## Quick start

Requires Python 3.10 or newer. Run these from the project root.

Windows (cmd or PowerShell):

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m app.main
```

macOS / Linux equivalents: `source .venv/bin/activate` instead of the activate line above; the other commands are the same (use `python3` if `python` is not Python 3).

You can also start it with uvicorn directly:

```
uvicorn app.main:app --port 8000
```

Then open http://127.0.0.1:8000/ . It redirects to a fresh board URL such as `http://127.0.0.1:8000/board/<id>`. Share that URL with your colleagues.

A liveness check is available at `/healthz`.

## Configuration

Settings are environment variables read by `python -m app.main` (HOST and PORT) and by the `/` redirect (POTLUCK_BOARD_ID).

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Interface to bind. The app has NO authentication, so only set `HOST=0.0.0.0` on a trusted network. |
| `PORT` | `8000` | Port to listen on. |
| `POTLUCK_BOARD_ID` | unset | Pins one stable shared board. Every visit to `/` redirects to `/board/<that id>`, so the office can use a single permanent link. When unset, each visit to `/` creates a new board. |
| `POTLUCK_TIMEZONE` | `Asia/Kolkata` | IANA timezone that defines "today" for the past-date check and automatic cleanup. An invalid name falls back to the default with a warning. |
| `POTLUCK_PURGE_MINUTES` | `10` | How often the cleanup runs (minimum 1). Invalid values use the default. |

Examples:

```
:: cmd
set POTLUCK_BOARD_ID=pirnav-2026
python -m app.main
```

```
# PowerShell
$env:POTLUCK_BOARD_ID = "pirnav-2026"
python -m app.main
```

macOS / Linux: `POTLUCK_BOARD_ID=pirnav-2026 python -m app.main`.

Note: `HOST` and `PORT` are only read by `python -m app.main`. If you start with `uvicorn`, pass `--host` and `--port` instead.

## Automatic cleanup of past-dated items

Items are deleted automatically once their date has passed:

- An item expires when its date is earlier than today's date in `POTLUCK_TIMEZONE` (default `Asia/Kolkata`). Items dated today stay all day.
- Claimed items expire too. Legacy items with no date never expire.
- The cleanup runs once at startup and then every `POTLUCK_PURGE_MINUTES` minutes (default 10). Each removed item is broadcast as an `ItemDeleted` event, so open boards drop the card live.
- On Render's free plan the service sleeps when idle, so the cleanup also runs when it wakes up.
- Adding an item with a date in the past is rejected with a 400 ("The date can't be in the past.").
- On Windows the IANA timezone data comes from the pinned `tzdata` package in `requirements.txt`.

## How sharing works

The board id in the URL is the access key. Boards are created on first use, and anyone who has the link `/board/<id>` can view the board, add items, and claim items. Use the Share tab in the UI to copy the link.

To let colleagues on the same office network connect:

1. Start the server listening on all interfaces:
   - cmd: `set HOST=0.0.0.0` then `python -m app.main`
   - PowerShell: `$env:HOST = "0.0.0.0"` then `python -m app.main`
   - or with uvicorn: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
2. Allow Python through the Windows firewall if the prompt appears.
3. Give colleagues `http://<your-ip>:8000/board/<id>` (find your IP with `ipconfig`).

Only do this on a network you trust, because the app has no authentication.

## Banner image

Save the PIRNAV Potluck Week 2026 banner at exactly this path and name:

```
static/images/potluck-banner.jpeg
```

When the file is present it is used as the main hero image. When it is missing, the page shows a CSS-built version of the same theme, so nothing breaks.

## Project layout

```
app/
  main.py          FastAPI app, static files, / redirect, board page, entrypoint
  api.py           REST routes, WebSocket endpoint, error mapping
  service.py       Business logic (add, claim, unclaim, people, board state)
  store.py         In-memory board store and id generation
  pg_store.py      Postgres board store (used when DATABASE_URL is set)
  hub.py           WebSocket hub that broadcasts changes per board
  models.py        Data models
  exceptions.py    Domain errors
static/
  index.html       Single-page frontend
  css/theme.css    Potluck Week theme
  js/              Frontend API and realtime client modules
  images/          Holds potluck-banner.jpeg (hero image)
tests/             Test package
requirements.txt   Pinned dependencies
```

## API

All routes are relative to the server root. `<board_id>` is created lazily on first use.

| Method | Path | Description | Success |
| --- | --- | --- | --- |
| GET | `/` | Redirect to `/board/<id>` (new id, or `POTLUCK_BOARD_ID`) | 307 |
| GET | `/board/<board_id>` | The web UI | 200 |
| GET | `/board/<board_id>/state` | Full snapshot: items and people | 200 |
| GET | `/board/<board_id>/people` | Who is bringing what | 200 |
| POST | `/board/<board_id>/items` | Add an item. Body: `{"title": "...", "image_url": "..."}` (`image_url` optional) | 201 |
| POST | `/board/<board_id>/items/<item_id>/claim` | Claim an item. Body: `{"name": "..."}` | 200 |
| POST | `/board/<board_id>/items/<item_id>/unclaim` | Release an item you claimed. Body: `{"name": "..."}` | 200 |
| WS | `/board/<board_id>/ws` | Live change events for the board | n/a |
| GET | `/healthz` | Liveness probe | 200 |

Error responses are JSON with a `detail` field:

- 400: validation error (for example a blank title or name)
- 403: unclaim by someone other than the current claimer
- 404: board item not found
- 409: item already claimed; the body also includes `current_claimer`

WebSocket: on connect the server sends `{"type": "subscribed", "board_id": "..."}`, then pushes change events as items are added, claimed or unclaimed. Send the text `ping` and the server replies `pong`. Clients should fetch `/state` first and again after any reconnect.

## Running tests

```
python -m pytest -q
```

The suite covers the store, hub, service, REST API and WebSocket fan-out, and uses Hypothesis property tests for the correctness properties in the design (status consistency, single claimer, claim exclusivity, people view, unclaim ownership). Hypothesis keeps a `.hypothesis` cache folder in the project root.

## Running tests

```
python -m pytest -q
```

The suite covers the store, hub, service (including property-based tests for the correctness properties), the REST API, and realtime fan-out between two WebSocket clients.

## Storage: in-memory or Postgres

By default boards live in memory and are lost on restart. To persist them in Postgres, set the `DATABASE_URL` environment variable before starting the app; unset it to go back to in-memory. The app prints `storage: postgres` or `storage: memory` at startup (never the URL), and creates its tables (`potluck_boards`, `potluck_items`) automatically.

```
:: cmd
set DATABASE_URL=<your postgres connection string>
python -m app.main
```

```
# PowerShell
$env:DATABASE_URL = "<your postgres connection string>"
python -m app.main
```

Never commit the connection string. `.env` is in `.gitignore`. The Postgres tests in `tests/test_pg_store.py` run only when `DATABASE_URL` is set, and clean up just their own rows.

## Deploying to Render + Neon (free)

1. Push this repo to GitHub.
2. Create a Neon project and copy its connection string (the `-pooler` one is fine).
3. In Render, create a Web Service from the repo, or use New > Blueprint, which reads `render.yaml`. Build: `pip install -r requirements.txt`. Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Health check path: `/healthz`.
4. In the Render dashboard, set the `DATABASE_URL` environment variable to the Neon connection string. Never commit it. `render.yaml` declares the variable with `sync: false` so the value is only entered in the dashboard.
5. Run exactly one worker. The WebSocket hub and the claim locks are in-process, so do not add `--workers` or scale to multiple instances.

Notes:

- On the free plan the service sleeps after about 15 minutes without traffic and takes a short while to wake on the next request. Open WebSocket pings from a browser tab keep it awake. Boards now persist in Neon, so a sleep or restart no longer loses data.
- There is no sign-in. Leave `POTLUCK_BOARD_ID` unset (each visit to `/` creates a new unguessable board) or set it to a long random value, because anyone who knows the id can edit that board.
- If the Neon connection string was ever shared or pasted somewhere public, rotate the Neon role password and update `DATABASE_URL` in Render.

## Known limitations

- Data is in memory unless `DATABASE_URL` is set. With in-memory storage, restarting the server clears all boards.
- Single server process only. The realtime hub is in-process, so do not run multiple workers or instances behind a load balancer.
- No authentication. Anyone with a board link can change that board.
- Claim names are self-declared. Nothing verifies who is typing a name.
