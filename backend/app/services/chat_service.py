"""Monta o system prompt do assistente de criacao de prompts e delega a
conversa ao cliente LLM. Nao gera imagem nenhuma - so ajuda o usuario a
escrever o texto que vai para o campo de prompt da geracao.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.clients.llm_client import ChatMessage, OllamaClient
from app.persona_manager.manager import Persona, PersonaManager

BASE_SYSTEM_PROMPT = """Voce e um assistente que ajuda o usuario a escrever prompts para \
geracao de imagens com IA (modelo Flux Kontext). Seu trabalho e conversar com o usuario \
sobre a cena que ele quer gerar (roupa, cenario, pose, iluminacao, camera, humor) e, ao \
final, propor um prompt pronto em portugues ou ingles, em UMA frase, usando linguagem \
fotografica (ex: "fotografia realista", "luz natural", "85mm", "profundidade de campo") \
para maximizar realismo. Sempre que propuser um prompt final, coloque-o sozinho em uma \
linha comecando com "PROMPT:" para que o app consiga extrai-lo automaticamente."""

PERSONA_SYSTEM_PROMPT_SUFFIX = """

O usuario esta gerando imagens da persona "{name}". As caracteristicas fixas dela \
(rosto, cabelo, pele, corpo) ja sao inseridas automaticamente pelo sistema - NUNCA \
descreva ou repita essas caracteristicas no prompt final, e avise o usuario se ele \
tentar descrever algo que conflite com a identidade dela (ex: outra cor de cabelo). \
Foque a conversa apenas nos elementos variaveis: roupa, cenario, pose, iluminacao, \
expressao, camera."""


class ChatService:
    def __init__(self, llm_client: OllamaClient, persona_manager: PersonaManager) -> None:
        self.llm_client = llm_client
        self.persona_manager = persona_manager

    def _system_prompt(self, persona: Persona | None) -> str:
        if persona is None:
            return BASE_SYSTEM_PROMPT
        return BASE_SYSTEM_PROMPT + PERSONA_SYSTEM_PROMPT_SUFFIX.format(name=persona.name)

    async def reply(self, persona_id: str | None, history: list[ChatMessage]) -> tuple[str, str]:
        persona = self.persona_manager.get_persona(persona_id) if persona_id else None
        system = ChatMessage(role="system", content=self._system_prompt(persona))
        return await self.llm_client.chat([system, *history])
