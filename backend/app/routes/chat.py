from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.clients.llm_client import ChatMessage, LLMConnectionError, LLMError, LLMTimeoutError
from app.jobs import JobRegistry
from app.persona_manager.manager import PersonaNotFoundError

router = APIRouter()
_jobs = JobRegistry()


class ChatMessageBody(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1)


class ChatBody(BaseModel):
    persona_id: str | None = None
    messages: list[ChatMessageBody] = Field(..., min_length=1)


@router.post("/chat")
async def chat(body: ChatBody, request: Request):
    chat_service = request.app.state.chat_service
    # Conversar tambem conta como uso: sem isso o auto-desligamento derruba
    # o pod no meio de um chat so porque nao houve geracao de imagem.
    request.app.state.idle_shutdown.touch()

    history = [ChatMessage(role=m.role, content=m.content) for m in body.messages]

    try:
        reply, model = await chat_service.reply(body.persona_id, history)
    except PersonaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    request.app.state.idle_shutdown.touch()
    return {"role": "assistant", "content": reply, "model": model}


@router.post("/chat/jobs")
async def start_chat_job(body: ChatBody, request: Request):
    """Mesmo chat, raciocinando antes de responder (entende melhor pedidos
    longos). Demora mais que o limite do proxy: consulta em /chat/jobs/{id}."""
    chat_service = request.app.state.chat_service
    request.app.state.idle_shutdown.touch()
    if body.persona_id:
        try:
            request.app.state.persona_manager.get_persona(body.persona_id)
        except PersonaNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    history = [ChatMessage(role=m.role, content=m.content) for m in body.messages]

    async def work():
        reply, model = await chat_service.reply(body.persona_id, history, think=True)
        return {"role": "assistant", "content": reply, "model": model}

    return _jobs.start(work)


@router.get("/chat/jobs/{job_id}")
async def get_chat_job(job_id: str, request: Request):
    request.app.state.idle_shutdown.touch()
    return _jobs.get(job_id)
