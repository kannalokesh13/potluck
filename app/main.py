"""Application entrypoint.

Creates the FastAPI app with one shared store, hub and service, mounts the API
router, and serves the single-page frontend from ``static/``.

Run with either::

    python -m app.main
    uvicorn app.main:app
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .api import create_router, register_exception_handlers
from .hub import WebSocketHub
from .service import PotluckService
from .store import BoardStore, InMemoryBoardStore, generate_board_id

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"
INDEX_HTML = STATIC_DIR / "index.html"

# Make sure the mount never fails at import time, even before the frontend exists.
STATIC_DIR.mkdir(parents=True, exist_ok=True)


def load_env_file(path: Path) -> None:
    """Load ``KEY=VALUE`` lines from a local ``.env`` file, if one exists.

    Handy for local runs so settings like ``DATABASE_URL`` do not have to be
    re-typed in every terminal. Variables already set in the real environment
    always win, so hosts such as Render (which have no ``.env`` file) are
    unaffected. Values are never printed. ``.env`` is git-ignored.
    """
    if os.getenv("POTLUCK_NO_DOTENV") or not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


load_env_file(PROJECT_ROOT / ".env")


def build_store() -> BoardStore:
    """Pick the storage backend.

    Postgres is opt-in: it is used only when ``DATABASE_URL`` is set. The URL
    is read from the environment and is never printed or logged; only the
    backend name is reported.
    """
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        # Lazy import so psycopg is only required when Postgres is configured.
        from .pg_store import PostgresBoardStore

        print("storage: postgres", flush=True)
        return PostgresBoardStore(database_url)
    print("storage: memory", flush=True)
    return InMemoryBoardStore()


store = build_store()
hub = WebSocketHub()
service = PotluckService(store, hub)


DEFAULT_PURGE_MINUTES = 10


def purge_interval_seconds() -> float:
    """Seconds between cleanups from ``POTLUCK_PURGE_MINUTES`` (default 10, min 1)."""
    raw = os.getenv("POTLUCK_PURGE_MINUTES")
    minutes = DEFAULT_PURGE_MINUTES
    if raw:
        try:
            minutes = max(1, int(raw.strip()))
        except ValueError:
            minutes = DEFAULT_PURGE_MINUTES
    return minutes * 60.0


async def _safe_purge() -> None:
    """Run one cleanup; log (never raise) on failure."""
    try:
        removed = await service.purge_expired()
        if removed:
            print(f"cleanup: removed {len(removed)} expired item(s)", flush=True)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Only the exception type is logged: messages could carry connection details.
        print(f"cleanup failed: {type(exc).__name__}", flush=True)


async def _purge_loop(interval: float) -> None:
    """Repeat the cleanup forever; per-cycle errors never kill the loop."""
    while True:
        await asyncio.sleep(interval)
        await _safe_purge()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Purge expired items at startup, then periodically; close the store on exit."""
    await _safe_purge()
    task = asyncio.create_task(_purge_loop(purge_interval_seconds()))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        close = getattr(store, "close", None)
        if callable(close):
            close()


app = FastAPI(title="Realtime Potluck Planner", lifespan=lifespan)
register_exception_handlers(app)

# API routes are registered first. ``GET /board/{board_id}`` (the UI) below does
# not shadow them: the API routes are all longer paths (/state, /items, /people,
# /ws), and Starlette matches on the full path.
app.include_router(create_router(service, hub))

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Redirect to a board.

    If ``POTLUCK_BOARD_ID`` is set, everyone lands on that one pinned board, so
    an office can share a single stable link. Otherwise a fresh unguessable id
    is generated per visit, so the creator must share the resulting URL.
    """
    board_id = os.getenv("POTLUCK_BOARD_ID") or generate_board_id()
    return RedirectResponse(url=f"/board/{board_id}", status_code=307)


@app.get("/board/{board_id}", include_in_schema=False)
async def board_page(board_id: str) -> Response:
    """Serve the SPA entry point for any board id."""
    if not INDEX_HTML.is_file():
        return PlainTextResponse(
            "The potluck UI isn't available yet. Please check back shortly.",
            status_code=503,
        )
    return FileResponse(INDEX_HTML, media_type="text/html")


if __name__ == "__main__":
    import uvicorn

    # The app has no authentication (by product decision), so bind to loopback
    # by default. Set HOST=0.0.0.0 explicitly to expose it on the network.
    # Pass the app object (not an import string) so this module is not imported a
    # second time, which would open a second database connection pool.
    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
