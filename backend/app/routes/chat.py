from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.clients.llm_client import ChatMessage, LLMConnectionError, LLMError
from app.persona_manager.manager import PersonaNotFoundError

router = APIRouter()


class ChatMessageBody(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1)


class ChatBody(BaseModel):
    persona_id: str | None = None
    messages: list[ChatMessageBody] = Field(..., min_length=1)


@router.post("/chat")
async def chat(body: ChatBody, request: Request):
    chat_service = request.app.state.chat_service

    history = [ChatMessage(role=m.role, content=m.content) for m in body.messages]

    try:
        reply = await chat_service.reply(body.persona_id, history)
    except PersonaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"role": "assistant", "content": reply}
