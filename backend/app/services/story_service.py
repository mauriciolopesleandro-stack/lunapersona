"""Historia em fotos: a pessoa cola uma historia (em portugues) e o modelo de
chat do pod planeja a serie de fotos - uma "biblia" fixa (lugares, roupas de
cada parte, outros personagens, luz) e um prompt por foto que repete essas
descricoes palavra por palavra. E isso que mantem a consistencia entre as
fotos: o gerador nao lembra da foto anterior, so do texto de cada uma.

A identidade da persona fica com a LoRA dela (o prompt so diz quem e pelo
gatilho); o resto da geracao e a mesma da tela Gerar.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.clients.comfyui_client import ComfyUIClient
from app.clients.llm_client import ChatMessage, LLMResponseError, OllamaClient
from app.persona_manager.manager import PersonaManager

MAX_PHOTOS = 20
# Historia longa + biblia + 20 prompts cabem (o padrao do Ollama corta).
CONTEXT_TOKENS = 16384
LLM_VRAM_BYTES = 13 * 1024**3

_SYSTEM = """You are the photo director of a photo story starring {name}, a {age}-year-old Brazilian woman. \
In every prompt she is called exactly "{trigger}" (a trained model already knows her face, hair and body).

You receive a story in Portuguese and plan a series of exactly {count} realistic photos that tell it in order, \
like one photographer following the same day. Consistency between photos is the whole point.

Step 1 - STORY BIBLE (fixed descriptions, in English):
- locations: each place with a short fixed visual description (colors, furniture, style, Brazilian details);
- outfits: what {trigger} wears in each part of the story (exact garments and colors) - only change when the story changes it;
- characters: every other recurring person with a fixed physical description and clothing (age, build, hair, skin, outfit);
- light: time of day and lighting for each part;
- camera: one photographic style for the whole series (e.g. "candid smartphone photo, natural light").

Step 2 - one prompt per photo (in English, 45 to 90 words). Each prompt:
- starts with the framing and the action (e.g. "medium shot of {trigger} laughing while ...");
- copies the location, the outfit and the other characters' descriptions VERBATIM from the bible;
- says where she looks and what her hands hold;
- ends with the light and the camera style from the bible;
- never describes {trigger}'s face, hair color, skin or body (the model already knows them);
- varies the framing across the series (close-up, medium, full body, over the shoulder...).

Reply with JSON only, no comments, in this exact shape:
{{"bible": {{"locations": ["..."], "outfits": ["..."], "characters": ["..."], "light": "...", "camera": "..."}},
 "scenes": [{{"title": "titulo curto em portugues", "summary": "o que acontece, em portugues, 1 frase", "prompt": "..."}}]}}"""

_JSON = re.compile(r"\{.*\}", re.DOTALL)


class StoryService:
    def __init__(
        self,
        llm_client: OllamaClient,
        persona_manager: PersonaManager,
        comfyui_client: ComfyUIClient | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.persona_manager = persona_manager
        self.comfyui_client = comfyui_client

    async def plan(self, persona_id: str, story: str, count: int) -> dict[str, Any]:
        persona = self.persona_manager.get_persona(persona_id)
        trigger = persona.lora.trigger if persona.lora else persona.name
        count = max(1, min(MAX_PHOTOS, count))
        system = _SYSTEM.format(name=persona.name, age=25, trigger=trigger, count=count)
        if self.comfyui_client is not None:
            await self.comfyui_client.free_memory(need_bytes=LLM_VRAM_BYTES)
        reply, model = await self.llm_client.chat(
            [ChatMessage("system", system), ChatMessage("user", story.strip())],
            think=True,
            num_ctx=CONTEXT_TOKENS,
            timeout=900.0,
            keep_alive="0",
        )
        plan = _parse(reply)
        scenes = [
            {
                "title": str(s.get("title", "")).strip() or f"Foto {i + 1}",
                "summary": str(s.get("summary", "")).strip(),
                "prompt": _with_trigger(str(s.get("prompt", "")).strip(), trigger),
            }
            for i, s in enumerate(plan.get("scenes") or [])
            if str(s.get("prompt", "")).strip()
        ][:count]
        if not scenes:
            raise LLMResponseError("O modelo nao devolveu nenhuma cena. Tente de novo ou encurte a historia.")
        return {"bible": plan.get("bible") or {}, "scenes": scenes, "model": model}


def _parse(reply: str) -> dict[str, Any]:
    match = _JSON.search(reply or "")
    if not match:
        raise LLMResponseError("O modelo nao devolveu o plano em JSON. Tente de novo.")
    try:
        data = json.loads(match.group(0))
    except ValueError as exc:
        raise LLMResponseError(f"Plano em JSON invalido ({exc}). Tente de novo.") from exc
    if not isinstance(data, dict):
        raise LLMResponseError("Plano em formato inesperado. Tente de novo.")
    return data


def _with_trigger(prompt: str, trigger: str) -> str:
    """Garante o gatilho da LoRA no prompt (sem ele a persona nao aparece)."""
    return prompt if trigger.lower() in prompt.lower() else f"{trigger}, {prompt}"
