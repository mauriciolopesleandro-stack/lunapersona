"""Voz da persona com VoxCPM2 (portugues do Brasil bem mais natural que o
Qwen3-TTS, que soava robotico).

O VoxCPM2 roda fora do ComfyUI, num venv proprio instalado por
scripts/setup_voxcpm.sh (o autostart do pod instala em segundo plano), via
scripts/voxcpm_tts.py. Os audios saem na pasta output/voice do ComfyUI, entao
o site os toca pelo mesmo /view das imagens.

1. design: cria opcoes de voz a partir de uma descricao, todas falando a
   mesma frase.
2. save_voice: a opcao escolhida vira a voz oficial da persona
   (personas/<id>/voice/, sincronizada entre volumes com a persona).
3. speak: qualquer texto novo clona essa referencia (audio + o texto falado
   nele), entao a persona fala sempre com a mesma voz.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.clients.comfyui_client import ComfyUIClient
from app.clients.llm_client import OllamaClient
from app.persona_manager.manager import PersonaManager
from app.services.prompt_translator import to_english
from app.workflow_manager.manager import WorkflowParamError

VOXCPM_PYTHON = Path(os.environ.get("VOXCPM_PYTHON", "/root/voxcpm/venv/bin/python"))
VOXCPM_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "voxcpm_tts.py"
COMFY_OUTPUT = Path(os.environ.get("COMFYUI_OUTPUT_DIR", "/workspace/runpod-slim/ComfyUI/output"))
SUBFOLDER = "voice"
DESIGN_OPTIONS = 3
DEFAULT_SAMPLE_TEXT = "Oi, eu sou a Luna! Que bom te ver por aqui. Hoje eu quero te mostrar um lugar incrível, vem comigo!"
# Carregar o modelo leva ~30 s; cada frase, alguns segundos.
VOICE_TIMEOUT = 600.0
# VRAM livre antes de abrir o VoxCPM2 (~8 GB de pico).
VOXCPM_VRAM_BYTES = 9 * 1024**3


class VoiceService:
    def __init__(
        self,
        comfyui_client: ComfyUIClient,
        persona_manager: PersonaManager,
        llm_client: OllamaClient | None = None,
    ) -> None:
        self.comfyui_client = comfyui_client
        self.persona_manager = persona_manager
        self.llm_client = llm_client
        # Um processo de voz por vez: dois juntos disputariam a GPU.
        self._lock = asyncio.Lock()

    def _output(self, name: str) -> dict[str, Any]:
        return {
            "filename": name,
            "subfolder": SUBFOLDER,
            "type": "output",
            "url": self.comfyui_client.build_image_url(name, SUBFOLDER, "output"),
        }

    async def _run_voxcpm(self, jobs: list[dict[str, Any]]) -> None:
        if not VOXCPM_PYTHON.exists():
            raise WorkflowParamError(
                "A voz ainda esta sendo instalada neste servidor (leva uns 5 minutos depois que ele liga). "
                "Tente de novo daqui a pouco."
            )
        (COMFY_OUTPUT / SUBFOLDER).mkdir(parents=True, exist_ok=True)
        async with self._lock:
            # Descarrega os modelos do ComfyUI: o VoxCPM2 precisa de ~8 GB de VRAM.
            await self.comfyui_client.free_memory(need_bytes=VOXCPM_VRAM_BYTES)
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
                json.dump(jobs, f, ensure_ascii=False)
                jobs_path = f.name
            try:
                proc = await asyncio.create_subprocess_exec(
                    str(VOXCPM_PYTHON), str(VOXCPM_SCRIPT), jobs_path,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                try:
                    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=VOICE_TIMEOUT)
                except asyncio.TimeoutError:
                    proc.kill()
                    raise WorkflowParamError(f"A voz passou de {VOICE_TIMEOUT:.0f}s e foi cancelada.")
            finally:
                os.unlink(jobs_path)
        if proc.returncode != 0:
            tail = stderr.decode(errors="replace").strip().splitlines()[-3:]
            raise WorkflowParamError("Falha ao gerar a voz: " + " | ".join(tail))

    async def design(self, description: str, text: str) -> dict[str, Any]:
        """Gera DESIGN_OPTIONS vozes diferentes para a mesma descricao."""
        text = text.strip() or DEFAULT_SAMPLE_TEXT
        # A descricao vai em ingles (o modelo segue melhor); a fala fica em portugues.
        instruct = await to_english(self.llm_client, description)
        if not instruct:
            raise WorkflowParamError("Descreva a voz que voce quer.")
        batch = uuid.uuid4().hex[:8]
        jobs = []
        for i in range(DESIGN_OPTIONS):
            seed = uuid.uuid4().int % (2**31)
            name = f"voice_design_{batch}_{i + 1}.wav"
            jobs.append({"text": text, "description": instruct, "seed": seed, "out": str(COMFY_OUTPUT / SUBFOLDER / name)})
        start = time.monotonic()
        await self._run_voxcpm(jobs)
        options = [
            {**self._output(Path(j["out"]).name), "seed": j["seed"]}
            for j in jobs
            if Path(j["out"]).exists()
        ]
        return {"text": text, "instruct": instruct, "options": options, "duration_seconds": time.monotonic() - start}

    async def save_voice(
        self, persona_id: str, filename: str, subfolder: str, text: str, description: str
    ) -> dict[str, Any]:
        content = await self.comfyui_client.download_file(filename, subfolder, "output")
        persona = self.persona_manager.set_voice(
            persona_id, Path(filename).suffix or ".wav", content, text, description
        )
        return asdict(persona.voice)

    async def speak(self, persona_id: str, text: str) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise WorkflowParamError("Escreva o que a persona deve falar.")
        persona = self.persona_manager.get_persona(persona_id)
        ref = self.persona_manager.get_voice_path(persona_id)
        if not persona.voice or not ref:
            raise WorkflowParamError(f"A persona '{persona_id}' ainda nao tem voz. Crie uma na aba Voz.")
        name = f"{persona_id}_fala_{uuid.uuid4().hex[:8]}.wav"
        job = {
            "text": text,
            "ref_wav": str(ref),
            "ref_text": persona.voice.text,
            "seed": uuid.uuid4().int % (2**31),
            "out": str(COMFY_OUTPUT / SUBFOLDER / name),
        }
        start = time.monotonic()
        await self._run_voxcpm([job])
        return {"text": text, "audios": [self._output(name)], "duration_seconds": time.monotonic() - start}
