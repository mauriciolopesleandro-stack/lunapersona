"""Palavras que dizem se o pedido (ja em ingles) fala de expressao ou roupa.
Usado pelo PromptBuilder e pelo GenerationService."""
from __future__ import annotations

import re

# Se o pedido ja fala de expressao/olhar, a atitude padrao da persona nao entra.
EXPRESSION_WORDS = re.compile(
    r"\b(?:smil\w*|laugh\w*|grin\w*|expression|surpris\w*|shock\w*|mouth|wink\w*|pout\w*|serious|sad|angry|"
    r"cry\w*|tongue|scream\w*|gaze|frown\w*|kiss\w*|looking)\b",
    re.I,
)
# Pedido ja diz a roupa?
CLOTHES_WORDS = re.compile(
    r"\b(?:wear\w*|dress\w*|outfit|clothes|clothing|shirt|t-shirt|top|blouse|jacket|coat|sweater|hoodie|jeans|pants|"
    r"trousers|shorts|skirt|bikini|swimsuit|lingerie|robe|uniform|suit|leggings|sportswear|naked|nude)\b",
    re.IGNORECASE,
)
