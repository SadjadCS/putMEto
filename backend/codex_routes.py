"""Fixed local endpoints for the PutMeTo assistant; no arbitrary Codex RPC."""

from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from backend.codex_bridge import CodexError, get_bridge


router = APIRouter(prefix="/api/codex")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Message(Input):
    message: str = Field(min_length=1, max_length=20000)


class Answer(Input):
    id: str = Field(min_length=1, max_length=100)
    approved: bool | None = None
    answers: dict[Annotated[str, StringConstraints(min_length=1, max_length=100)],
                  Annotated[str, StringConstraints(max_length=10000)]] | None = None


async def call(operation, status=409):
    try:
        return await operation
    except CodexError as exc:
        raise HTTPException(status, str(exc)) from exc


@router.get("/status")
async def status():
    return await get_bridge().get_status()


@router.post("/login")
async def login():
    return await call(get_bridge().login(), 503)


@router.get("/chat")
def chat():
    return get_bridge().chat_state()


@router.post("/chat/message", status_code=202)
async def message(body: Message):
    return await call(get_bridge().send_message(body.message))


@router.post("/chat/reset")
async def reset():
    return await call(get_bridge().reset())


@router.post("/chat/cancel")
async def cancel():
    return await call(get_bridge().cancel())


@router.post("/chat/respond")
async def respond(body: Answer):
    try:
        return get_bridge().respond(body.id, approved=body.approved, answers=body.answers)
    except CodexError as exc:
        raise HTTPException(409, str(exc)) from exc
