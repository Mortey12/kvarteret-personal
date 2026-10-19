"""Admin-only agent chat. Stateless: the browser holds the conversation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
from app.observability import log_admin_activity
from app.web.templates import templates

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


@router.post("/agent/chat")
async def agent_chat(
    body: ChatRequest,
    current_user: AuthenticatedUser = Depends(require_admin_user),
    service: AgentChatService = Depends(get_agent_chat_service),
):
    if body.messages[-1].role != "user":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Last message must be from the user.")
    try:
        result = await service.chat(
            [m.model_dump() for m in body.messages], admin_id=current_user.user_account_id,
            distinct_id=str(current_user.auth_user_id),
        )
    except AgentUnavailableError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Skonk er utilgjengelig.")
    return {"reply": result.reply, "proposals": result.proposals}


@router.post("/agent/apply")
async def agent_apply(
    request: Request,
    body: ApplyRequest,
    current_user: AuthenticatedUser = Depends(require_admin_user),
    service: AgentChatService = Depends(get_agent_chat_service),
):
    try:
        message = await service.apply(body.token, admin_id=current_user.user_account_id)
    except InvalidProposalError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    log_admin_activity(
        request=request, user=current_user, action="agent.apply",
        subject_type="agent_proposal", details={},
    )
    return {"message": message}
