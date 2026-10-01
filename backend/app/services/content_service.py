"""Assistente de conteudo da persona para redes sociais: ideias, roteiros,
falas, legendas e pedidos de foto/video, conversando com o dono da persona.

Tudo que a assistente sabe da persona fica na pasta personas/<id>/conteudo/
(fora do Git, sincronizada entre volumes com a persona):
- perfil.json: o "manual" (quem ela e, personalidade, jeito de falar,
  publico, redes, limites) - editavel no site;
- memoria.json: fatos que a assistente aprendeu conversando (linhas
  "MEMORIA:" das respostas);
- conversa.json: a conversa atual (continua entre aparelhos e sessoes).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.clients.comfyui_client import ComfyUIClient
from app.clients.llm_client import ChatMessage, OllamaClient
from app.persona_manager.manager import PersonaManager

MAX_MEMORY = 120
MAX_CONVERSATION = 80
CONTEXT_MESSAGES = 30
# Conversa inteira + manual + memoria cabem (o padrao do Ollama, ~4 mil
# tokens, cortava o comeco e o modelo "esquecia" o que foi pedido).
CONTEXT_TOKENS = 16384
# Modelo de chat + conversa longa na GPU: o ComfyUI solta a placa antes.
LLM_VRAM_BYTES = 13 * 1024**3


@dataclass
class ContentProfile:
    bio: str = ""
    personalidade: str = ""
    jeito_de_falar: str = ""
    publico: str = ""
    redes: str = ""
    nicho: str = ""
    limites: str = ""


DEFAULT_PROFILES: dict[str, ContentProfile] = {
    "luna": ContentProfile(
        bio=(
            "Luna (marca Luna Vox), 25 anos, criadora de conteudo brasileira de Sao Paulo. Vive a cidade: "
            "Avenida Paulista, cafes, rooftop com piscina, baladas de funk, livros sobre disciplina, viagens. "
            "Valoriza conteudo, viagens e liberdade."
        ),
        personalidade="Ousada, romantica, misteriosa e engracada. Confiante, provoca com charme e humor.",
        jeito_de_falar=(
            "Portugues do Brasil bem natural, jeito paulistano, frases curtas, girias leves (tipo 'gente', "
            "'mano', 'amo'), duplo sentido sutil e bem-humorado, deixa um misterio no ar."
        ),
        publico="Principalmente homens, mas tambem fala com mulheres.",
        redes="Todas (Instagram, TikTok, X, YouTube Shorts e outras) - adaptar o formato a cada rede.",
        nicho="Aberto - definir conversando com o dono da Luna.",
        limites="Nunca racista, preconceituosa ou com discurso de odio. Sempre adulta (25 anos).",
    )
}

_SYSTEM = """Voce e a estrategista de conteudo e roteirista da {name}, uma influenciadora virtual \
brasileira. Voce conversa com o DONO da {name} (nao com os seguidores) e o ajuda a criar \
conteudo para as redes dela: ideias, calendario, roteiros de reels/videos, falas, legendas, \
ganchos, hashtags e as fotos e videos que o estudio vai gerar.

MANUAL DA {name_upper}
- Quem ela e: {bio}
- Personalidade: {personalidade}
- Jeito de falar: {jeito_de_falar}
- Publico: {publico}
- Redes: {redes}
- Nicho: {nicho}
- Limites (obrigatorios): {limites}

O QUE VOCE JA APRENDEU CONVERSANDO (memoria):
{memoria}

COMO TRABALHAR
- Converse SEMPRE em portugues do Brasil, de forma direta, criativa e pratica. Trate a pessoa \
por "voce" (nunca de "dono").
- Falas e legendas devem soar como a {name} (personalidade e jeito de falar acima).
- Cenarios sempre em lugares reais do Brasil (ex.: Avenida Paulista, Vila Madalena, Ibirapuera, \
praias do Rio, Floripa), com detalhes que deixem a cena crivel.
- MEMORIA: sempre que a pessoa disser algo novo sobre nicho, foco, redes, publico, estilo, \
frequencia de posts, o que funcionou ou nao, ou preferencias, termine a resposta com uma linha \
por fato novo, por exemplo:
  MEMORIA: Prefere postar 3 vezes por semana, a noite.
  Nao repita o que ja esta na memoria acima.
- Quando sugerir algo para o estudio produzir, use linhas proprias com estes prefixos (o site \
transforma cada uma em botao):
  FOTO: <pedido da foto em INGLES, uma frase: enquadramento, roupa COMPLETA (parte de cima e de \
baixo), cenario brasileiro real, acao, luz. NUNCA fale de rosto, cabelo, pele, corpo ou expressao \
dela - o estudio ja coloca sozinho. Ex.: FOTO: full body photo, white linen shirt and denim \
shorts, sitting at a cafe table on Avenida Paulista holding a coffee cup, golden hour light>
  VIDEO: <movimento do video em INGLES, uma frase curta e realista>
  FALA: <o que ela fala no video, em portugues, ate uns 25 segundos. So as palavras faladas: sem \
hashtags, emojis, aspas ou indicacoes de cena - vira audio direto>
  LEGENDA: <legenda pronta para postar, com gancho e hashtags>
- Num roteiro, numere as cenas e para cada cena de as linhas FOTO / VIDEO / FALA que fizerem sentido.
- Escreva cada FOTO/VIDEO/FALA/LEGENDA numa linha so, comecando pelo prefixo (sem negrito).
- Seja objetiva: nada de textos longos sem necessidade."""

_MEMORY_REMINDER = (
    "(Lembrete: se nesta mensagem eu contei algo novo sobre nicho, foco, redes, publico, estilo ou "
    "preferencias, termine sua resposta com uma linha 'MEMORIA: <fato>' para cada fato novo.)"
)

# Aceita negrito/lista que o modelo as vezes poe: "- **MEMORIA:** fato".
_MEMORY_LINE = re.compile(r"^[\s*-]*MEM[OÓ]RIA\**\s*:\s*\**\s*(.+?)[\s*]*$", re.I | re.M)


@dataclass
class ContentMessage:
    role: str
    content: str
    at: float = field(default_factory=time.time)


class ContentService:
    def __init__(
        self,
        llm_client: OllamaClient,
        persona_manager: PersonaManager,
        comfyui_client: ComfyUIClient | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.persona_manager = persona_manager
        self.comfyui_client = comfyui_client

    def _dir(self, persona_id: str) -> Path:
        self.persona_manager.get_persona(persona_id)  # 404 se nao existir
        path = self.persona_manager.personas_dir / persona_id / "conteudo"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _read(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    @staticmethod
    def _write(path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_profile(self, persona_id: str) -> ContentProfile:
        saved = self._read(self._dir(persona_id) / "perfil.json", None)
        if saved is None:
            return DEFAULT_PROFILES.get(persona_id, ContentProfile())
        known = {k: v for k, v in saved.items() if k in ContentProfile.__dataclass_fields__}
        return ContentProfile(**known)

    def save_profile(self, persona_id: str, profile: ContentProfile) -> ContentProfile:
        self._write(self._dir(persona_id) / "perfil.json", asdict(profile))
        return profile

    def get_memory(self, persona_id: str) -> list[dict[str, Any]]:
        return self._read(self._dir(persona_id) / "memoria.json", [])

    def delete_memory(self, persona_id: str, index: int) -> list[dict[str, Any]]:
        memory = self.get_memory(persona_id)
        if 0 <= index < len(memory):
            memory.pop(index)
            self._write(self._dir(persona_id) / "memoria.json", memory)
        return memory

    def _add_memory(self, persona_id: str, facts: list[str]) -> list[str]:
        memory = self.get_memory(persona_id)
        known = {m["text"].strip().lower() for m in memory}
        added = []
        for fact in facts:
            fact = fact.strip()
            if fact and fact.lower() not in known:
                memory.append({"text": fact, "at": time.time()})
                known.add(fact.lower())
                added.append(fact)
        self._write(self._dir(persona_id) / "memoria.json", memory[-MAX_MEMORY:])
        return added

    def get_conversation(self, persona_id: str) -> list[dict[str, Any]]:
        return self._read(self._dir(persona_id) / "conversa.json", [])

    def clear_conversation(self, persona_id: str) -> None:
        self._write(self._dir(persona_id) / "conversa.json", [])

    def _system_prompt(self, persona_id: str) -> str:
        persona = self.persona_manager.get_persona(persona_id)
        profile = self.get_profile(persona_id)
        memory = self.get_memory(persona_id)
        return _SYSTEM.format(
            name=persona.name,
            name_upper=persona.name.upper(),
            memoria="\n".join(f"- {m['text']}" for m in memory) or "- (nada ainda)",
            **asdict(profile),
        )

    async def send(self, persona_id: str, text: str) -> dict[str, Any]:
        conversation = self.get_conversation(persona_id)
        conversation.append(asdict(ContentMessage("user", text.strip())))
        history = [ChatMessage(m["role"], m["content"]) for m in conversation[-CONTEXT_MESSAGES:]]
        # Lembrete so na ultima mensagem (nao fica salvo): so no prompt de
        # sistema o modelo quase nunca escrevia as linhas MEMORIA.
        history[-1] = ChatMessage("user", f"{history[-1].content}\n\n{_MEMORY_REMINDER}")
        if self.comfyui_client is not None:
            await self.comfyui_client.free_memory(need_bytes=LLM_VRAM_BYTES)
        reply, model = await self.llm_client.chat(
            [ChatMessage("system", self._system_prompt(persona_id)), *history],
            think=True,
            num_ctx=CONTEXT_TOKENS,
            timeout=600.0,
        )
        added = self._add_memory(persona_id, _MEMORY_LINE.findall(reply))
        conversation.append(asdict(ContentMessage("assistant", reply)))
        self._write(self._dir(persona_id) / "conversa.json", conversation[-MAX_CONVERSATION:])
        return {"reply": reply, "model": model, "memory_added": added}
