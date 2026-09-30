"""Traduz para ingles o texto que a pessoa escreve na tela de gerar.

O encoder de texto do Chroma (T5) entende mal portugues: "tomando banho
apos um dia de praia" vira ruido e a cena sai confusa. O modelo de chat que
ja roda no pod (Ollama) faz a traducao antes de montar o prompt.

Texto que ja esta em ingles nao passa pelo modelo. Qualquer falha (Ollama
fora, demora) devolve o texto original - a geracao nunca para por isso.
"""
from __future__ import annotations

import logging
import re

from app.clients.llm_client import ChatMessage, LLMError, OllamaClient

_SYSTEM = (
    "You translate prompts for an image generator into English. "
    "The input may mix Portuguese and English: translate every Portuguese word, so the whole reply is in English. "
    "Reply with only the English text on a single line: no quotes, no notes, no explanations. "
    "Keep every detail exactly as written; do not add, remove, censor or soften anything. "
    "Keep names and the word lunavox as they are."
)
# Exemplo de texto misturado: sem ele o modelo devolvia o texto igual,
# achando que "ja estava em ingles".
_EXAMPLE = [
    ChatMessage("user", "same scene, realistic photo com as pernas cruzadas e a mão no joelho, natural lighting"),
    ChatMessage("assistant", "same scene, realistic photo, with her legs crossed and her hand on her knee, natural lighting"),
]
_ACCENTS = re.compile(r"[ãõçáéíóúâêôà]", re.I)
_PT_WORDS = {
    "de", "da", "do", "das", "dos", "com", "uma", "um", "em", "na", "no", "nas", "nos", "para", "que",
    "ela", "sem", "muito", "foto", "praia", "cabelo", "roupa", "vestido", "sentada", "deitada", "quero",
    "sol", "noite", "dia", "casa", "quarto", "cama", "rua", "olhando", "vermelho", "preto", "branco", "azul",
}
log = logging.getLogger(__name__)
_WORD = re.compile(r"[a-zA-ZÀ-ÿ]+")
# Sem a GPU presa: a imagem e gerada logo em seguida e precisa da VRAM.
_KEEP_ALIVE = "0"
# O 1o uso num pod novo carrega o modelo do volume de rede (passa de 1 min).
_TIMEOUT = 120.0


def looks_portuguese(text: str) -> bool:
    if _ACCENTS.search(text):
        return True
    words = [w.lower() for w in _WORD.findall(text)]
    return sum(w in _PT_WORDS for w in words) >= 2


async def to_english(llm: OllamaClient | None, text: str) -> str:
    text = text.strip()
    if not llm or not text or not looks_portuguese(text):
        return text
    try:
        answer, _ = await llm.chat(
            [ChatMessage("system", _SYSTEM), *_EXAMPLE, ChatMessage("user", text)],
            keep_alive=_KEEP_ALIVE,
            timeout=_TIMEOUT,
        )
    except LLMError as exc:
        log.warning("Traducao do prompt falhou, seguindo com o original: %s", exc)
        return text
    answer = " ".join(answer.split()).strip("\"' ")
    # Resposta vazia ou muito maior que o original = o modelo conversou em vez
    # de traduzir; melhor ficar com o texto da pessoa.
    if not answer or len(answer) > 3 * len(text) + 40:
        log.warning("Traducao do prompt descartada (%d caracteres)", len(answer))
        return text
    return answer
