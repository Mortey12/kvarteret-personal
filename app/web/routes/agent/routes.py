"""Admin-only agent chat. Stateless: the browser holds the conversation."""

from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.models import AuthenticatedUser
from app.dependencies import get_agent_chat_service, require_admin_user
from app.domain.agent_chat.service import (
    MAX_MESSAGE_CHARS,
    MAX_MESSAGES,
    AgentChatService,
    AgentUnavailableError,
    InvalidProposalError,
)
from app.observability import get_logger, log_admin_activity
from app.web.csrf import CSRF_COOKIE_NAME
from app.web.routes.agent.actions import AdminActionCatalog
from app.web.templates import templates

logger = get_logger(__name__)
router = APIRouter(include_in_schema=False)


class Message(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=MAX_MESSAGES)


class ApplyRequest(BaseModel):
    token: str = Field(max_length=4000)


@router.get("/agent")
async def agent_page(
    request: Request,
    current_user: AuthenticatedUser = Depends(require_admin_user),
    service: AgentChatService = Depends(get_agent_chat_service),
):
    return templates.TemplateResponse(
        request,
        "pages/agent/agent.html",
        {
            "title": "Skonk",
            "section": "agent",
            "current_user": current_user,
            "configured": service.configured,
        },
    )


_STATUS_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


@router.post("/agent/chat")
async def agent_chat(
    request: Request,
    body: ChatRequest,
    current_user: AuthenticatedUser = Depends(require_admin_user),
    service: AgentChatService = Depends(get_agent_chat_service),
):
    """Stream newline-delimited JSON events (step, delta, tool, proposal, done, error)."""
    if body.messages[-1].role != "user":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Last message must be from the user.")
    if not service.configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Skonk er ikke konfigurert.")
    catalog = AdminActionCatalog(request.app)
    messages = [m.model_dump() for m in body.messages]

    async def events():
        try:
            async for event in service.chat_stream(
                messages,
                admin_id=current_user.user_account_id,
                distinct_id=str(current_user.auth_user_id),
                actions=catalog,
            ):
                yield json.dumps(event, default=str) + "\n"
        except AgentUnavailableError:
            yield json.dumps({"type": "error", "message": "Skonk er utilgjengelig."}) + "\n"
        except Exception:
            logger.exception("agent_chat.failed", extra={"event": "agent_chat.failed"})
            yield json.dumps({"type": "error", "message": "Noe gikk galt."}) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson", headers=_STATUS_HEADERS)


def _internal_runner(request: Request):
    """Run a confirmed action through the real route, as this admin."""

    async def run(method: str, path: str, form: dict[str, list[str]]) -> str:
        transport = httpx.ASGITransport(app=request.app)
        headers = {
            "cookie": request.headers.get("cookie", ""),
            "x-csrf-token": request.cookies.get(CSRF_COOKIE_NAME, ""),
        }
        async with httpx.AsyncClient(
            transport=transport, base_url=str(request.base_url), follow_redirects=False
        ) as client:
            response = await client.request(method, path, data=form, headers=headers)
        if response.status_code >= 400:
            detail = response.text[:300]
            try:
                detail = response.json().get("detail", detail)
            except ValueError:
                pass
            raise InvalidProposalError(f"Handlingen feilet ({response.status_code}): {detail}")
        where = response.headers.get("location")
        return "Utført." + (f" ({where})" if where else "")

    return run


@router.post("/agent/apply")
async def agent_apply(
    request: Request,
    body: ApplyRequest,
    current_user: AuthenticatedUser = Depends(require_admin_user),
    service: AgentChatService = Depends(get_agent_chat_service),
):
    try:
        message = await service.apply(
            body.token,
            admin_id=current_user.user_account_id,
            runner=_internal_runner(request),
        )
    except InvalidProposalError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    log_admin_activity(
        request=request, user=current_user, action="agent.apply",
        subject_type="agent_proposal", details={},
    )
    return {"message": message}
