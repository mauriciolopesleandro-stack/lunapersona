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
from app.services.reference_caption import clean_reference_caption
from app.services.scene_describer import describe_image
from app.workflow_manager.manager import WorkflowManager

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

Each bible item must be complete on its own (a character entry always has the name, "man" or "woman", \
age, build, face (eyes, nose, beard), hair, skin and clothes - e.g. "Rafa, a 28-year-old man, slim build, ...").

Step 2 - for each photo:
- "action" (English, 20 to 45 words): the framing and what happens - e.g. "medium shot of {trigger} laughing \
while she lifts a tray from the oven, looking at Rafa, both hands holding the tray" - with where she looks and \
what her hands hold. Do NOT describe the place, the clothes, the other people's looks or {trigger}'s face, hair, \
skin or body here (they are added from the bible);
- "location": index of the place in bible.locations; "outfit": index in bible.outfits;
- "characters": indexes in bible.characters of the other people visible in the photo ([] if she is alone);
- "light": the light of this moment, short;
- vary the framing across the series (close-up, medium, full body, over the shoulder...).

FRAMING - the photos are {shape}. Pick framings that fit this shape so nobody is cut by the frame:
{shape_rule}
- name the framing first ("full body shot", "medium shot from the waist up", "close-up of their faces") and say \
what is fully in frame ("both of them fully in frame, heads and feet visible");
- never put an important hand or object at the edge of the frame.

TOUCH - when people touch, describe the contact exactly and simply: which hand, where it rests, how \
(e.g. "his right hand resting lightly on her waist", "their fingers interlaced", "her head leaning on his \
shoulder", "she holds his forearm with her left hand"). One clear point of contact per photo, relaxed and \
natural, bodies turned toward each other, the contact visible to the camera (not hidden behind a body).

LANGUAGE: everything in the bible and every "action" must be written in ENGLISH (the image model only \
understands English), even though the story is in Portuguese. Only "title" and "summary" are in Portuguese.

Reply with JSON only, no comments, in this exact shape:
{{"bible": {{"locations": ["..."], "outfits": ["..."], "characters": ["..."], "light": "...", "camera": "..."}},
 "scenes": [{{"title": "titulo curto em portugues", "summary": "o que acontece, em portugues, 1 frase", \
"action": "...", "location": 0, "outfit": 0, "characters": [0], "light": "..."}}]}}"""

# Formato da foto -> enquadramentos que cabem nele (sem isso o modelo pedia
# "duas pessoas lado a lado de corpo inteiro" numa foto 9:16 e cortava gente).
_SHAPES = {
    "9:16": ("tall vertical 9:16 (phone stories)",
             "- one person: full body standing or medium shot; two people: close together, one slightly in front of "
             "the other or face to face, medium shot from the waist up or full body standing close - never side by "
             "side far apart, never lying down across the frame."),
    "4:5": ("vertical 4:5 (feed)",
            "- one person: any framing; two people: close together, medium shot or full body standing close - not "
            "far apart side by side."),
    "1:1": ("square 1:1",
            "- medium shots work best; two people close together; full body only if they are sitting or close."),
    "16:9": ("wide horizontal 16:9",
             "- two people side by side, sitting or lying fit well; one person: medium shot or full body with "
             "space around; avoid tall full body close-ups."),
}

# Pack de fotos -> historia: cada foto vira uma cena, na mesma ordem.
_FROM_PHOTOS = (
    "The input below is not a story: it is the description of each photo of a photo shoot, in order. "
    "Make exactly one photo per description, in the same order. In every photo the main woman is {trigger}. "
    "Keep each photo's place, clothes, pose, action, framing and the other people (as characters in the bible, "
    "described in your own words) - the series must tell the same story as the shoot.\n\n"
)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
# palavras que so aparecem se o modelo escreveu a biblia/acao em portugues
_PT_WORDS = re.compile(r"\b(de|com|uma|anos|cabelo|pele|vestido|camisa|cozinha|mesa|luz|segurando|olhando)\b", re.IGNORECASE)
_ENGLISH_REMINDER = (
    "\n\nIMPORTANT: write the bible and every action in ENGLISH (only title and summary in Portuguese)."
)


class StoryService:
    def __init__(
        self,
        llm_client: OllamaClient,
        persona_manager: PersonaManager,
        comfyui_client: ComfyUIClient | None = None,
        workflow_manager: WorkflowManager | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.persona_manager = persona_manager
        self.comfyui_client = comfyui_client
        self.workflow_manager = workflow_manager

    async def plan_from_photos(self, persona_id: str, images: list[str], shape: str = "9:16") -> dict[str, Any]:
        """Pack de fotos -> historia: o Florence descreve cada foto (sem os
        tracos da pessoa) e o planejamento faz uma cena nova por foto. As fotos
        novas sao geradas do zero - nada das originais e reaproveitado."""
        if self.comfyui_client is None or self.workflow_manager is None:
            raise LLMResponseError("Descricao de fotos indisponivel neste backend.")
        persona = self.persona_manager.get_persona(persona_id)
        trigger = persona.lora.trigger if persona.lora else persona.name
        lines = []
        for i, image in enumerate(images[:MAX_PHOTOS]):
            caption = clean_reference_caption(await describe_image(self.comfyui_client, self.workflow_manager, image))
            lines.append(f"Photo {i + 1}: {caption or 'no description'}")
        return await self.plan(persona_id, _FROM_PHOTOS.format(trigger=trigger) + "\n".join(lines), len(lines), shape)

    async def plan(self, persona_id: str, story: str, count: int, shape: str = "9:16") -> dict[str, Any]:
        persona = self.persona_manager.get_persona(persona_id)
        trigger = persona.lora.trigger if persona.lora else persona.name
        count = max(1, min(MAX_PHOTOS, count))
        label, rule = _SHAPES.get(shape, _SHAPES["9:16"])
        system = _SYSTEM.format(name=persona.name, age=25, trigger=trigger, count=count, shape=label, shape_rule=rule)
        if self.comfyui_client is not None:
            await self.comfyui_client.free_memory(need_bytes=LLM_VRAM_BYTES)
        story = story.strip()
        for attempt in range(2):
            reply, model = await self.llm_client.chat(
                [ChatMessage("system", system), ChatMessage("user", story)],
                think=True,
                num_ctx=CONTEXT_TOKENS,
                timeout=900.0,
                keep_alive="0",
            )
            try:
                plan = _parse(reply)
            except LLMResponseError:
                # resposta quebrada (JSON cortado ou com sobra): tenta de novo
                if attempt:
                    raise
                continue
            # historia em portugues -> as vezes o modelo responde tudo em
            # portugues e o gerador de imagem entende bem menos: pede de novo
            if not _in_portuguese(plan):
                break
            story += _ENGLISH_REMINDER
        bible = plan.get("bible") or {}
        scenes = []
        for i, s in enumerate(plan.get("scenes") or []):
            prompt = _assemble(s, bible, trigger, persona.name)
            if prompt:
                scenes.append({
                    "title": str(s.get("title", "")).strip() or f"Foto {i + 1}",
                    "summary": str(s.get("summary", "")).strip(),
                    "prompt": prompt,
                })
        scenes = scenes[:count]
        if not scenes:
            raise LLMResponseError("O modelo nao devolveu nenhuma cena. Tente de novo ou encurte a historia.")
        return {"bible": bible, "scenes": scenes, "model": model}


def _in_portuguese(plan: dict[str, Any]) -> bool:
    bible = plan.get("bible") or {}
    text = " ".join(
        [str(x) for key in ("locations", "outfits", "characters") for x in (bible.get(key) or [])]
        + [str(s.get("action") or "") for s in plan.get("scenes") or [] if isinstance(s, dict)]
    )
    return len(_PT_WORDS.findall(text)) >= 4


def _pick(items: Any, index: Any) -> str:
    try:
        return str(items[int(index)]).strip()
    except (TypeError, ValueError, IndexError, KeyError):
        return ""


def _is_persona(text: str, trigger: str, persona_name: str) -> bool:
    head = text.strip().lower()
    return head.startswith(trigger.lower()) or bool(persona_name) and head.startswith(persona_name.lower())


def _assemble(scene: dict[str, Any], bible: dict[str, Any], trigger: str, persona_name: str = "") -> str:
    """Prompt da foto montado aqui, colando o texto completo da biblia: pedido
    ao modelo, ele resumia ("Rafa (black t-shirt)") e o rosto do outro mudava
    de uma foto para a outra. Plano antigo (so "prompt") ainda funciona."""
    action = str(scene.get("action") or "").strip()
    if not action:
        return _with_trigger(str(scene.get("prompt") or "").strip(), trigger) if scene.get("prompt") else ""
    parts = [_with_trigger(action, trigger)]
    outfit = _pick(bible.get("outfits"), scene.get("outfit"))
    if outfit:
        parts.append(f"{trigger} is wearing {outfit}")
    for idx in scene.get("characters") or []:
        who = _pick(bible.get("characters"), idx)
        # o modelo as vezes lista a propria persona ("Luna (... fair skin)"):
        # quem a descreve e a LoRA
        if who and not _is_persona(who, trigger, persona_name):
            parts.append(f"with {who}")
    place = _pick(bible.get("locations"), scene.get("location"))
    if place:
        parts.append(f"in {place}")
    light = str(scene.get("light") or bible.get("light") or "").strip()
    if light:
        parts.append(light)
    camera = str(bible.get("camera") or "").strip()
    if camera:
        parts.append(camera)
    return ", ".join(p.rstrip(" .,") for p in parts)


def _parse(reply: str) -> dict[str, Any]:
    """O plano e o objeto JSON com "scenes". A resposta as vezes traz texto ou
    um "{}" solto antes/depois - pegar do primeiro { ao ultimo } dava "Extra
    data". Aqui cada { e testado e fica o maior objeto valido com cenas."""
    text = _THINK.sub("", reply or "")
    decoder = json.JSONDecoder()
    best: dict[str, Any] | None = None
    best_len = 0
    error: ValueError | None = None
    for start in (i for i, ch in enumerate(text) if ch == "{"):
        try:
            data, end = decoder.raw_decode(text, start)
        except ValueError as exc:
            error = error or exc
            continue
        if isinstance(data, dict) and data.get("scenes") and end - start > best_len:
            best, best_len = data, end - start
    if best is not None:
        return best
    if error is not None:
        raise LLMResponseError(f"Plano em JSON invalido ({error}). Tente de novo.")
    raise LLMResponseError("O modelo nao devolveu o plano em JSON. Tente de novo.")


def _with_trigger(prompt: str, trigger: str) -> str:
    """Garante o gatilho da LoRA no prompt (sem ele a persona nao aparece)."""
    return prompt if trigger.lower() in prompt.lower() else f"{trigger}, {prompt}"
