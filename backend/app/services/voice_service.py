"""Voz da persona com Qwen3-TTS (custom node ComfyUI-Qwen-TTS, instalado por
scripts/setup_voice.sh).

1. design: cria opcoes de voz a partir de uma descricao (workflow
   voice-design), todas falando a mesma frase.
2. save_voice: a opcao escolhida vira a voz oficial da persona
   (personas/<id>/voice/, sincronizada entre volumes com a persona).
3. speak: qualquer texto novo clona essa referencia (workflow voice-clone),
   entao a persona fala sempre com a mesma voz.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.clients.comfyui_client import ComfyUIClient
from app.clients.llm_client import OllamaClient
from app.persona_manager.manager import PersonaManager
from app.services.prompt_translator import to_english
from app.workflow_manager.manager import WorkflowManager, WorkflowParamError

DESIGN_WORKFLOW = "voice-design"
CLONE_WORKFLOW = "voice-clone"
DESIGN_OPTIONS = 3
DEFAULT_SAMPLE_TEXT = "Oi, eu sou a Luna! Que bom te ver por aqui. Hoje eu quero te mostrar um lugar incrível."
# Fala e curta, mas o 1o uso ainda baixa/carrega o modelo na GPU.
VOICE_TIMEOUT = 600.0


class VoiceService:
    def __init__(
        self,
        comfyui_client: ComfyUIClient,
        workflow_manager: WorkflowManager,
        persona_manager: PersonaManager,
        llm_client: OllamaClient | None = None,
    ) -> None:
        self.comfyui_client = comfyui_client
        self.workflow_manager = workflow_manager
        self.persona_manager = persona_manager
        self.llm_client = llm_client

    async def _run(self, workflow_id: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        graph = self.workflow_manager.render(workflow_id, params)
        prompt_id = await self.comfyui_client.queue_prompt(graph)
        entry = await self.comfyui_client.wait_for_completion(prompt_id, timeout=VOICE_TIMEOUT)
        return [asdict(a) for a in self.comfyui_client.extract_images(entry)]

    async def design(self, description: str, text: str) -> dict[str, Any]:
        """Gera DESIGN_OPTIONS vozes diferentes para a mesma descricao."""
        text = text.strip() or DEFAULT_SAMPLE_TEXT
        # A descricao vai em ingles (o modelo entende melhor); a fala fica em portugues.
        instruct = await to_english(self.llm_client, description)
        if not instruct:
            raise WorkflowParamError("Descreva a voz que voce quer.")
        start = time.monotonic()
        options = []
        for _ in range(DESIGN_OPTIONS):
            seed = uuid.uuid4().int % (2**32)
            audios = await self._run(DESIGN_WORKFLOW, {"TEXT": text, "INSTRUCT": instruct, "SEED": seed})
            if audios:
                options.append({**audios[0], "seed": seed})
        return {
            "text": text,
            "instruct": instruct,
            "options": options,
            "duration_seconds": time.monotonic() - start,
        }

    async def save_voice(
        self, persona_id: str, filename: str, subfolder: str, text: str, description: str
    ) -> dict[str, Any]:
        content = await self.comfyui_client.download_file(filename, subfolder, "output")
        persona = self.persona_manager.set_voice(
            persona_id, Path(filename).suffix or ".mp3", content, text, description
        )
        return asdict(persona.voice)

    async def _upload_reference(self, persona_id: str) -> tuple[str, str]:
        persona = self.persona_manager.get_persona(persona_id)
        path = self.persona_manager.get_voice_path(persona_id)
        if not persona.voice or not path:
            raise WorkflowParamError(f"A persona '{persona_id}' ainda nao tem voz. Crie uma na aba Voz.")
        content = path.read_bytes()
        # Nome pelo conteudo: trocar a voz gera outro arquivo no input/ do ComfyUI.
        digest = hashlib.sha1(content).hexdigest()[:10]
        name = await self.comfyui_client.upload_image(f"voice_{persona_id}_{digest}{path.suffix}", content)
        return name, persona.voice.text

    async def speak(self, persona_id: str, text: str) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise WorkflowParamError("Escreva o que a persona deve falar.")
        ref_audio, ref_text = await self._upload_reference(persona_id)
        start = time.monotonic()
        audios = await self._run(
            CLONE_WORKFLOW,
            {
                "TEXT": text,
                "REF_AUDIO": ref_audio,
                "REF_TEXT": ref_text,
                "SEED": uuid.uuid4().int % (2**32),
                "FILENAME_PREFIX": f"voice/{persona_id}_fala",
            },
        )
        return {"text": text, "audios": audios, "duration_seconds": time.monotonic() - start}
