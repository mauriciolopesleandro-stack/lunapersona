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
final, propor um prompt pronto em UMA frase, usando linguagem fotografica (ex: \
"realistic photography", "natural light", "85mm", "shallow depth of field") para \
maximizar realismo.

IDIOMA: converse com o usuario SEMPRE em portugues do Brasil. Mas o prompt final deve \
ser escrito SEMPRE em INGLES (o modelo de imagem gera resultados melhores em ingles), \
mesmo que o usuario tenha descrito a cena em portugues.

Sempre que propuser um prompt final, coloque-o sozinho em uma linha comecando com \
"PROMPT:" seguido do prompt em ingles, para que o app consiga extrai-lo automaticamente. \
Logo abaixo, em outra linha comecando com "Traducao:", escreva a mesma frase em \
portugues para o usuario entender o que sera gerado. Exemplo:
PROMPT: Realistic photography of a woman sitting by a cafe window in the morning, natural light, 85mm, shallow depth of field.
Traducao: Fotografia realista de uma mulher sentada perto da janela de um cafe pela manha, luz natural, 85mm, fundo desfocado."""

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
