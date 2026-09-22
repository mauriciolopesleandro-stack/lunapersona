"""Cliente isolado para falar com o servidor LLM local (Ollama) usado pelo
chat de apoio a criacao de prompts.

Mesma logica do ComfyUIClient: so comunicacao HTTP, sem regra de negocio
(o system prompt/persona fica no servico que usa este cliente).
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx


class LLMError(Exception):
    """Erro base para problemas de comunicacao com o servidor LLM."""


class LLMConnectionError(LLMError):
    """Nao foi possivel conectar ao servidor LLM (nao instalado/nao rodando)."""


class LLMResponseError(LLMError):
    """O servidor LLM respondeu com erro ou formato inesperado."""


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


class OllamaClient:
    """Cliente para a API do Ollama (http://<host>:11434), rodando no mesmo
    pod que o backend - por isso normalmente aponta para 127.0.0.1.
    """

    def __init__(self, base_url: str, model: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def chat(self, messages: list[ChatMessage]) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.RequestError as exc:
            raise LLMConnectionError(
                f"Falha de rede ao conectar em {self.base_url}: {exc}. "
                "Verifique se o Ollama esta instalado e rodando no pod."
            ) from exc

        if resp.status_code != 200:
            raise LLMResponseError(
                f"Servidor LLM respondeu status {resp.status_code}: {resp.text}"
            )

        data = resp.json()
        message = data.get("message", {})
        content = message.get("content")
        if not content:
            raise LLMResponseError(f"Resposta do LLM sem conteudo: {data}")
        return content
