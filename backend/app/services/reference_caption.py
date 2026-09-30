"""Limpa a descricao automatica (Florence-2) de uma foto de referencia antes
de ela entrar no prompt de uma persona.

A descricao fala da pessoa da foto: cabelo loiro, olhos azuis, corpo magro,
tatuagem. Com a persona, esses tracos precisam sair - senao voltam a vencer
a LoRA. O que interessa manter e roupa, pose, expressao e cenario. Frases
sobre texto/logo na foto tambem saem: pedir texto faz o modelo escrever
letras embaralhadas na imagem.
"""
from __future__ import annotations

import re

_TEXT_SENTENCE = re.compile(
    r"\b(text|texts|reads|written|writing|caption|logo|watermark|words?|letters?|font|says|sign that)\b", re.I
)
_BODY = "slim|slender|thin|skinny|petite|muscular|athletic|toned|fit|curvy|voluptuous|chubby|plus-size"
_PERSON_TRAITS = [
    re.compile(
        r"\b(?:her|his|their|the woman's|the man's)?\s*(?:[\w-]+,?\s+){0,4}hair\b(?:\s+(?:that|which)\s+[^,.;]*)?",
        re.I,
    ),
    re.compile(r"\b(?:her|his)?\s*(?:[\w-]+\s+){0,2}eyes\b", re.I),
    re.compile(r"\b(?:[\w-]+\s+){0,2}(?:skin|complexion|tan|freckles)\b", re.I),
    re.compile(r"\b(?:an?\s+)?(?:small|large|visible)?\s*tattoos?\b(?:\s+on\s+(?:her|his)\s+[\w-]+)?", re.I),
    re.compile(
        rf"\b(?:an?\s+)?(?:(?:{_BODY})\s*,?\s*)+(?:body|figure|physique|build|waist|legs|stomach)?\b", re.I
    ),
    re.compile(r"\b(?:blonde|blond|brunette|redhead|red-haired|dark-haired|light-haired)\b", re.I),
]
_PREFIX = re.compile(r"^(?:the image (?:shows|is|features|depicts)|in (?:this|the) image,?|this is)\s+", re.I)
# Conectivos que ficaram orfaos ("she has and is wearing", "with a in").
_ORPHAN = re.compile(r"\b(?:has|with|and)\s+(?=(?:and|is|in|,|\.|$))", re.I)
_EMPTY_SENTENCE = re.compile(r"^(?:she|he|they)(?:\s+(?:has|have|is|are))?\s*[.!?]?$", re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def clean_reference_caption(caption: str) -> str:
    sentences = [s for s in _SENTENCE_SPLIT.split(caption.strip()) if s and not _TEXT_SENTENCE.search(s)]
    text = " ".join(sentences)
    for pattern in _PERSON_TRAITS:
        text = pattern.sub(" ", text)
    text = _ORPHAN.sub(" ", _PREFIX.sub("", text))
    sentences = [re.sub(r"\s{2,}", " ", s).strip() for s in _SENTENCE_SPLIT.split(text)]
    text = " ".join(s for s in sentences if s and not _EMPTY_SENTENCE.match(s))
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*(?:,\s*)+", ", ", text)
    text = re.sub(r"\s+\.", ".", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ,.")
