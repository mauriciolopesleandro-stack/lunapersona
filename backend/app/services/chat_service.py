"""Monta o system prompt do assistente de criacao de prompts e delega a
conversa ao cliente LLM. Nao gera imagem nenhuma - so ajuda o usuario a
escrever o texto que vai para o campo de prompt da geracao.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.clients.comfyui_client import ComfyUIClient
from app.clients.llm_client import ChatMessage, OllamaClient
from app.persona_manager.manager import Persona, PersonaManager

BASE_SYSTEM_PROMPT = """Voce e um assistente que ajuda o usuario a escrever prompts para geracao de imagens com IA (modelo Chroma1-HD). Seu trabalho e conversar com o usuario sobre a cena que ele quer gerar (roupa, cenario, pose, acao, luz, enquadramento) e, ao final, propor um prompt pronto em UMA frase.

O OBJETIVO E PARECER FOTO REAL DE CELULAR, nao ensaio profissional. Foto de influencer de verdade e tirada por uma amiga ou no espelho, com o celular: enquadramento um pouco torto ou descentralizado, momento espontaneo (no meio de uma acao, nao posando de modelo), fundo NITIDO e cheio de detalhes comuns do lugar real (gente passando, carros estacionados, placas, fios de poste, calcada irregular, mesas, objetos do dia a dia), luz comum do horario (sol forte do meio-dia, sombra, ceu nublado, luz de janela, luz de lampada a noite).
Use termos como: "candid smartphone photo", "taken by a friend", "everyday", "background in focus", e cite o lugar real do Brasil com detalhes.
NAO use, a menos que o usuario peca: "85mm", "shallow depth of field", "bokeh", "cinematic", "golden hour", "editorial", "fashion shoot", "studio", "perfect", "dramatic lighting" - esses termos deixam a foto com cara de campanha feita por IA.

IDIOMA: converse com o usuario SEMPRE em portugues do Brasil. Mas o prompt final deve ser escrito SEMPRE em INGLES (o modelo de imagem gera resultados melhores em ingles), mesmo que o usuario tenha descrito a cena em portugues.

Sempre que propuser um prompt final, coloque-o sozinho em uma linha comecando com "PROMPT:" seguido do prompt em ingles, para que o app consiga extrai-lo automaticamente. Logo abaixo, em outra linha comecando com "Traducao:", escreva a mesma frase em portugues para o usuario entender o que sera gerado. Exemplo:
PROMPT: Candid smartphone photo taken by a friend, woman in a fitted black t-shirt dress laughing while crossing the busy sidewalk of Avenida Paulista on a weekday afternoon, people and parked motorcycles around, newsstand and street signs, overcast daylight, background in focus.
Traducao: Foto espontanea de celular tirada por uma amiga, mulher de vestido preto justo rindo enquanto atravessa a calcada movimentada da Avenida Paulista numa tarde de semana, gente e motos estacionadas em volta, banca de jornal e placas, dia nublado, fundo nitido."""

PERSONA_SYSTEM_PROMPT_SUFFIX = """

O usuario esta gerando imagens da persona "{name}". As caracteristicas fixas dela \
(rosto, cabelo, pele, corpo) ja sao inseridas automaticamente pelo sistema - NUNCA \
descreva ou repita essas caracteristicas no prompt final, e avise o usuario se ele \
tentar descrever algo que conflite com a identidade dela (ex: outra cor de cabelo). \
Foque a conversa apenas nos elementos variaveis: roupa, cenario, pose, iluminacao, \
expressao, camera."""


# Conversa inteira cabe no que o modelo enxerga (o padrao do Ollama, ~4 mil
# tokens, cortava o comeco - inclusive estas instrucoes).
CONTEXT_TOKENS = 16384
LLM_VRAM_BYTES = 13 * 1024**3


class ChatService:
    def __init__(
        self,
        llm_client: OllamaClient,
        persona_manager: PersonaManager,
        comfyui_client: ComfyUIClient | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.persona_manager = persona_manager
        self.comfyui_client = comfyui_client

    def _system_prompt(self, persona: Persona | None) -> str:
        if persona is None:
            return BASE_SYSTEM_PROMPT
        return BASE_SYSTEM_PROMPT + PERSONA_SYSTEM_PROMPT_SUFFIX.format(name=persona.name)

    async def reply(
        self, persona_id: str | None, history: list[ChatMessage], think: bool = False
    ) -> tuple[str, str]:
        """think=True so pela rota com job (raciocinar passa dos ~100 s do proxy)."""
        persona = self.persona_manager.get_persona(persona_id) if persona_id else None
        system = ChatMessage(role="system", content=self._system_prompt(persona))
        if self.comfyui_client is not None:
            await self.comfyui_client.free_memory(need_bytes=LLM_VRAM_BYTES)
        return await self.llm_client.chat(
            [system, *history], think=think, num_ctx=CONTEXT_TOKENS, timeout=600.0 if think else None
        )
