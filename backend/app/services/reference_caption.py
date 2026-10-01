"""Limpa a descricao automatica (Florence-2) de uma foto de referencia antes
de ela entrar no prompt de uma persona.

A descricao fala da pessoa da foto: cabelo loiro, olhos azuis, corpo magro,
tatuagem. Com a persona, esses tracos precisam sair - senao voltam a vencer
a LoRA. O que interessa manter e roupa, pose, expressao, penteado e cenario.
Texto/logo na foto tambem sai: pedir texto faz o modelo escrever letras
embaralhadas na imagem. Quando da, sai so o trecho ("chapeu com a palavra
Dior" vira "chapeu") para nao perder a roupa descrita na mesma frase.
"""
from __future__ import annotations

import re

_TEXT_SENTENCE = re.compile(
    r"\b(text|texts|reads|written|writing|caption|logos?|watermark|words?|letters?|font|says|sign that)\b", re.I
)
_TEXT_CLAUSE = re.compile(
    r"\s*,?\s*\b(?:with|featuring|bearing|showing|that (?:says|reads)|reading)\s+(?:the\s+|a\s+|an\s+)?"
    # ate 3 adjetivos antes: "with a gold Gucci logo on the front"
    r"(?:[\w'-]+\s+){0,3}?(?:words?|text|logos?|letters?|lettering|brand(?:\s+name)?|writing|name)\b"
    r"[^,.;]*?(?=\s+and\s|[,.;]|$)",
    re.I,
)
# Marca citada vira logo desenhado na roupa (o PromptGen as vezes inventa uma).
_BRANDS = re.compile(
    r"\b(?:gucci|dior|chanel|prada|louis vuitton|versace|fendi|balenciaga|givenchy|calvin klein|"
    r"victoria'?s secret|nike|adidas|puma|supreme|off-white)\b\s*",
    re.I,
)
_QUOTED = re.compile(r"\s*[\"“][^\"”]*[\"”]")
# Palavras que nao sao adjetivo do traco: sem isso "She has long blonde hair"
# levava o sujeito junto e a frase ficava sem sentido.
_NOT_ADJ = r"(?!(?:she|he|they|her|his|their|has|have|had|is|are|was|and|with|wearing)\b)"
# Penteado depois de "hair": ai so os adjetivos saem e a tranca/coque fica.
_STYLE = (
    r"\s+(?:(?:that|which)\s+(?:is|are)\s+)?"
    r"(?:styled|pulled|tied|braided|worn|in\s+(?:a|an|two|braids|pigtails|cornrows))\b"
)
_BODY = "slim|slender|thin|skinny|petite|muscular|athletic|toned|fit|curvy|voluptuous|chubby|plus-size"
_BODY_RUN = rf"(?:(?:{_BODY})(?:\s*,\s*|\s+and\s+|\s+)?)+"
# Frases inteiras sobre a pessoa da foto que sobrariam sem sentido depois de
# tirar so os adjetivos ("her body is and with...", "are not visible").
_WHOLE_TRAITS = [
    re.compile(r"\b(?:her|his|their)\s+eyes\s+(?:are|were)\s+not\s+visible,?\s*(?:but\s+|and\s+)?", re.I),
    re.compile(rf"\b(?:her|his|their)\s+body\s+is\s+{_BODY_RUN}(?:,?\s*with\s[^.]*)?\.?\s*", re.I),
]
_PERSON_TRAITS = [
    re.compile(rf"\b(?:{_NOT_ADJ}[\w-]+,?\s+){{1,4}}(?=hair{_STYLE})", re.I),
    re.compile(
        rf"\b(?:her|his|their|the woman's|the man's)?\s*(?:{_NOT_ADJ}[\w-]+,?\s+){{0,4}}hair\b(?!{_STYLE})"
        r"(?:\s+(?:that|which)\s+[^,.;]*)?",
        re.I,
    ),
    re.compile(rf"\b(?:her|his)?\s*(?:{_NOT_ADJ}[\w-]+\s+){{0,2}}eyes\b", re.I),
    re.compile(rf"\b(?:{_NOT_ADJ}[\w-]+\s+){{0,2}}(?:skin|complexion|tan|freckles)\b", re.I),
    re.compile(r"\b(?:an?\s+)?(?:small|large|visible)?\s*tattoos?\b(?:\s+on\s+(?:her|his)\s+[\w-]+)?", re.I),
    re.compile(
        rf"\b(?:an?\s+)?{_BODY_RUN}(?:body|figure|physique|build|waist|legs|stomach)?\b", re.I
    ),
    re.compile(r"\b(?:blonde|blond|brunette|redhead|red-haired|dark-haired|light-haired)\b", re.I),
]
_PREFIX = re.compile(r"^(?:the image (?:shows|is|features|depicts)|in (?:this|the) image,?|this is)\s+", re.I)
# Conectivos que ficaram orfaos ("she has and is wearing", "with and").
_ORPHANS = [
    re.compile(r"\b(?:has|with)\s+and\s+", re.I),
    re.compile(r"\b(?:has|with)\s+(?=(?:is|in|,|\.|$))", re.I),
    re.compile(r"\band\s+(?=(?:and|,|\.|$))", re.I),
]
_EMPTY_SENTENCE = re.compile(r"^(?:(?:she|he|they)(?:\s+(?:has|have|is|are))?)?\s*[.!?]?$", re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


# Expressao da pessoa da foto ("surprised expression", "mouth open"): sai
# quando a persona usa a atitude dela no lugar.
_EXPRESSION = [
    # "is smiling at the camera and wearing" -> "is wearing"
    re.compile(
        r"\b(is\s+)(?:smiling|grinning|laughing|frowning|pouting|smirking)\b(?:\s+(?:at|towards|to)\s+the\s+camera)?\s+and\s+",
        re.I,
    ),
    re.compile(
        r"\s*,?\s*(?:and\s+)?(?:with\s+)?\b(?:a|an|her|his)\s+(?:[\w-]+,?\s+){0,3}"
        r"(?:expression|smile|grin|look on (?:her|his) face)\b(?:\s+on\s+(?:her|his)\s+face)?",
        re.I,
    ),
    re.compile(
        r"\s*,?\s*(?:and\s+)?(?:with\s+)?\b(?:her|his)\s+(?:mouth|lips)\s+(?:is\s+|are\s+)?"
        r"(?:slightly\s+|wide\s+)?(?:open|agape|parted|pursed|closed)\b",
        re.I,
    ),
    re.compile(
        r"\s*,?\s*(?:and\s+)?\b(?:she\s+|he\s+)?(?:is\s+)?(?:smiling|grinning|laughing|frowning|pouting|smirking)\b"
        r"(?:\s+(?:at|towards|to)\s+the\s+camera)?",
        re.I,
    ),
]


def clean_reference_caption(caption: str, keep_expression: bool = True) -> str:
    caption = _BRANDS.sub("", _QUOTED.sub("", _TEXT_CLAUSE.sub("", caption.strip())))
    sentences = [s for s in _SENTENCE_SPLIT.split(caption) if s and not _TEXT_SENTENCE.search(s)]
    text = " ".join(sentences)
    if not keep_expression:
        for pattern in _EXPRESSION:
            text = pattern.sub(r"\1" if pattern.groups else "", text)
    for pattern in _WHOLE_TRAITS:
        text = pattern.sub("", text)
    for pattern in _PERSON_TRAITS:
        text = pattern.sub(" ", text)
    text = _PREFIX.sub("", text)
    for pattern in _ORPHANS:
        text = pattern.sub(" ", text)
    sentences = [re.sub(r"\s{2,}", " ", s).strip() for s in _SENTENCE_SPLIT.split(text)]
    text = " ".join(s for s in sentences if s and not _EMPTY_SENTENCE.match(s))
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*(?:,\s*)+", ", ", text)
    text = re.sub(r",\s*\.", ".", text)
    text = re.sub(r"\s+\.", ".", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ,.")
