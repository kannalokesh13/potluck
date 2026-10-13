"""REST API and WebSocket routes.

Handlers are intentionally thin: all business logic lives in
:class:`PotluckService`. Domain errors are mapped to HTTP responses by exception
handlers registered via :func:`register_exception_handlers`:

- ``ValidationError`` -> 400 ``{detail}``
- ``NotFoundError``   -> 404 ``{detail}``
- ``ConflictError``   -> 409 ``{detail, current_claimer}``
- ``PermissionError`` -> 403 ``{detail}`` (the domain one, not the builtin)

``create_router(service, hub)`` builds a router bound to the given service and
hub so the app (and tests) can wire isolated instances.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .exceptions import (
    ConflictError,
    NotFoundError,
    PermissionError as DomainPermissionError,
    ValidationError,
)
from .hub import WebSocketHub, item_to_dict
from .models import BoardState, PersonContribution
from .service import PotluckService


SANITIZED_400 = "The request was not valid. Please check the fields and try again."


class AddItemRequest(BaseModel):
    """Body for adding an item.

    Fields are loosely typed so missing or wrongly typed values reach the
    service validator and produce a 400 rather than FastAPI's 422.
    """

    title: Any = None
    added_by: Any = None
    employee_id: Any = None
    bring_date: Any = None
    image_url: Any = None


class DeleteItemRequest(BaseModel):
    """Body for delete. The employee id is read from the JSON body only."""

    employee_id: Any = None


class NameRequest(BaseModel):
    """Body for claim/unclaim. Blank values are rejected by the service."""

    name: str


def person_to_dict(person: PersonContribution) -> dict[str, Any]:
    """Serialize a ``PersonContribution`` to a JSON-ready dict."""
    return {"name": person.name, "items": [item_to_dict(i) for i in person.items]}


def state_to_dict(state: BoardState) -> dict[str, Any]:
    """Serialize a ``BoardState`` snapshot to a JSON-ready dict."""
    return {
        "board_id": state.board_id,
        "items": [item_to_dict(i) for i in state.items],
        "people": [person_to_dict(p) for p in state.people],
    }


def register_exception_handlers(app: FastAPI) -> None:
    """Register handlers mapping domain errors to HTTP status codes."""

    @app.exception_handler(RequestValidationError)
    async def _bad_request(_: Request, __: RequestValidationError) -> JSONResponse:
        # Fixed text: never echo request data (it may contain an employee id).
        return JSONResponse(status_code=400, content={"detail": SANITIZED_400})

    @app.exception_handler(ValidationError)
    async def _validation(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "current_claimer": exc.current_claimer},
        )

    @app.exception_handler(DomainPermissionError)
    async def _forbidden(_: Request, exc: DomainPermissionError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})


def create_router(service: PotluckService, hub: WebSocketHub) -> APIRouter:
    """Build the API router bound to ``service`` and ``hub``."""
    router = APIRouter()

    @router.get("/board/{board_id}/state")
    async def get_state(board_id: str) -> dict[str, Any]:
        return state_to_dict(service.get_board_state(board_id))

    @router.get("/board/{board_id}/people")
    async def get_people(board_id: str) -> dict[str, Any]:
        people = service.list_people(board_id)
        return {"board_id": board_id, "people": [person_to_dict(p) for p in people]}

    @router.post("/board/{board_id}/items", status_code=201)
    async def add_item(board_id: str, body: AddItemRequest) -> dict[str, Any]:
        item = await service.add_item(
            board_id,
            body.title,
            body.added_by,
            body.employee_id,
            body.bring_date,
            body.image_url,
        )
        return item_to_dict(item)

    @router.post("/board/{board_id}/items/{item_id}/delete")
    async def delete_item(
        board_id: str, item_id: str, body: DeleteItemRequest | None = None
    ) -> dict[str, Any]:
        employee_id = body.employee_id if body is not None else None
        item = await service.delete_item(board_id, item_id, employee_id)
        return item_to_dict(item)

    @router.post("/board/{board_id}/items/{item_id}/claim")
    async def claim_item(
        board_id: str, item_id: str, body: NameRequest
    ) -> dict[str, Any]:
        item = await service.claim_item(board_id, item_id, body.name)
        return item_to_dict(item)

    @router.post("/board/{board_id}/items/{item_id}/unclaim")
    async def unclaim_item(
        board_id: str, item_id: str, body: NameRequest
    ) -> dict[str, Any]:
        item = await service.unclaim_item(board_id, item_id, body.name)
        return item_to_dict(item)

    @router.websocket("/board/{board_id}/ws")
    async def board_ws(websocket: WebSocket, board_id: str) -> None:
        await websocket.accept()
        hub.subscribe(board_id, websocket)
        try:
            await websocket.send_text(
                json.dumps({"type": "subscribed", "board_id": board_id})
            )
            while True:
                message = await websocket.receive_text()
                if message.strip().lower() == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            pass
        finally:
            hub.unsubscribe(websocket)

    return router
