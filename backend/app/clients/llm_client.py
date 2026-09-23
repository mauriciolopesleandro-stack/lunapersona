"""Cliente isolado para falar com o servidor LLM local (Ollama) usado pelo
chat de apoio a criacao de prompts.

Mesma logica do ComfyUIClient: so comunicacao HTTP, sem regra de negocio
(o system prompt/persona fica no servico que usa este cliente).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

# Modelos com "raciocinio" (ex: qwen3, deepseek-r1) podem devolver o
# pensamento interno dentro do content - nao faz sentido mostrar isso no chat.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class LLMError(Exception):
    """Erro base para problemas de comunicacao com o servidor LLM."""


class LLMConnectionError(LLMError):
    """Nao foi possivel conectar ao servidor LLM (nao instalado/nao rodando)."""


class LLMTimeoutError(LLMError):
    """O servidor LLM aceitou a conexao mas demorou demais para responder."""


class LLMResponseError(LLMError):
    """O servidor LLM respondeu com erro ou formato inesperado."""


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


class OllamaClient:
    """Cliente para a API do Ollama (http://<host>:11434), rodando no mesmo
    pod que o backend - por isso normalmente aponta para 127.0.0.1.

    O nome do modelo vem de LLM_MODEL (.env). Se LLM_MODEL estiver vazio,
    usa o primeiro modelo instalado no Ollama (GET /api/tags) - assim o
    modelo instalado manualmente no pod funciona sem mexer no codigo.
    """

    def __init__(self, base_url: str, model: str, timeout: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout = timeout

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
        except httpx.RequestError as exc:
            raise LLMConnectionError(
                f"Falha de rede ao conectar em {self.base_url}: {exc}. "
                "Verifique se o Ollama esta rodando no pod."
            ) from exc
        if resp.status_code != 200:
            raise LLMResponseError(f"Ollama /api/tags respondeu status {resp.status_code}: {resp.text}")
        return [m.get("name", "") for m in resp.json().get("models", []) if m.get("name")]

    async def resolve_model(self) -> str:
        if self.model:
            return self.model
        installed = await self.list_models()
        if not installed:
            raise LLMResponseError(
                "LLM_MODEL nao esta definido e nenhum modelo esta instalado no Ollama "
                "(ollama list vazio)."
            )
        return installed[0]

    async def chat(self, messages: list[ChatMessage]) -> tuple[str, str]:
        """Retorna (resposta, nome do modelo usado)."""
        model = await self.resolve_model()
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            # Desliga o "raciocinio" de modelos como qwen3: sem isso a resposta
            # demora bem mais e pode estourar o limite de ~100s do proxy RunPod.
            "think": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload)
                if resp.status_code == 400 and "think" in resp.text.lower():
                    # Modelo sem suporte a "think" - repete sem o campo.
                    payload.pop("think")
                    resp = await client.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"O Ollama nao respondeu em {self.timeout:.0f}s (modelo '{model}'). "
                "Na primeira mensagem o modelo ainda esta sendo carregado na memoria - "
                "tente de novo em alguns segundos. Ajuste LLM_TIMEOUT no .env se precisar."
            ) from exc
        except httpx.RequestError as exc:
            raise LLMConnectionError(
                f"Falha de rede ao conectar em {self.base_url}: {exc}. "
                "Verifique se o Ollama esta instalado e rodando no pod."
            ) from exc

        if resp.status_code == 404:
            # Ollama responde 404 quando o modelo pedido nao esta instalado.
            try:
                installed = ", ".join(await self.list_models()) or "nenhum"
            except LLMError:
                installed = "nao foi possivel listar"
            raise LLMResponseError(
                f"Modelo '{model}' nao encontrado no Ollama ({resp.text.strip()}). "
                f"Modelos instalados: {installed}. Ajuste LLM_MODEL no .env do pod."
            )
        if resp.status_code != 200:
            raise LLMResponseError(
                f"Servidor LLM respondeu status {resp.status_code}: {resp.text}"
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMResponseError(f"Resposta do LLM nao e JSON valido: {resp.text[:500]}") from exc
        message = data.get("message") or {}
        content = _THINK_BLOCK.sub("", message.get("content") or "").strip()
        if not content:
            raise LLMResponseError(f"Resposta do LLM sem conteudo: {data}")
        return content, data.get("model") or model
