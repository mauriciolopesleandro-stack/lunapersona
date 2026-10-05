import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.clients.comfyui_client import ComfyUIClient
from app.clients.llm_client import OllamaClient
from app.config import get_settings
from app.idle_shutdown import IdleShutdownTracker
from app.model_manager.manager import ModelManager
from app.persona_manager.manager import PersonaManager
from app.jobs import running_jobs
from app import notify
from app.core.generation.history import GenerationHistory
from app.core.generation.negative import NegativePromptBuilder
from app.core.generation.orchestrator import GenerationOrchestrator
from app.core.generation.prompt_builder import PromptBuilder
from app.core.generation.retry_policy import RetryPolicy
from app.core.persona import PersonaRepository
from app.core.persona.references import ReferenceManager
from app.core.persona.sheet import PersonaSheetRepository
from app.core.telemetry import CostEstimator
from app.core.validation.checks import (
    AgeValidator,
    AnatomyValidator,
    BodyConsistencyValidator,
    FaceIdentityValidator,
    PoseValidator,
    SubjectCountValidator,
    TriggerLeakValidator,
)
from app.core.validation.engine import ValidationEngine
from app.infrastructure.runpod import RunPodProvider
from app.providers.base import ProviderRegistry
from app.providers.comfyui import comfyui_provider_set
from app.services.prompt_translator import to_english
from app.validation_backends.comfyui import ComfyImageAnalyzer, FlorenceTextReader
from app.routes import (
    chat, content, generate, health, models, persona_engine, personas, story, video, voice, workflows,
)
from app.security import token_middleware
from app.services.chat_service import ChatService
from app.services.generation_service import GenerationService
from app.services.scene_describer import SceneDescriber
from app.services.swap_service import SwapService
from app.services.talk_service import TalkService
from app.services.video_service import VideoService
from app.services.content_service import ContentService
from app.services.story_service import StoryService
from app.services.voice_service import VoiceService
from app.workflow_manager.manager import WorkflowManager

settings = get_settings()

app = FastAPI(title="Luna AI Studio - Backend", version="0.1.0")

# Antes do CORS: o ultimo middleware adicionado e o mais externo, e a resposta
# 401 tambem precisa dos cabecalhos de CORS para o site ler o erro.
app.middleware("http")(token_middleware(settings.luna_api_token))
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.settings = settings
app.state.comfyui_client = ComfyUIClient(
    base_url=settings.comfyui_url,
    api_key=settings.comfyui_api_key,
    connect_timeout=settings.comfyui_connect_timeout,
    generation_timeout=settings.comfyui_generation_timeout,
)
app.state.workflow_manager = WorkflowManager(settings.workflows_dir)
app.state.model_manager = ModelManager(settings.models_registry_path)
app.state.persona_manager = PersonaManager(settings.personas_dir)
app.state.persona_repository = PersonaRepository(settings.personas_dir)
# Persona Sheet = fonte de verdade da persona (personas/<id>/persona_sheet.json).
app.state.persona_sheets = PersonaSheetRepository(settings.personas_dir)
# Masters da ficha nao podem ser removidas, trocadas nem alteradas.
app.state.reference_manager = ReferenceManager(app.state.persona_manager, app.state.persona_sheets.protected_reference_ids)
# Etapas do Persona Engine V1 (cena, Face Lock, pose). Provider novo = registrar aqui.
app.state.provider_registry = ProviderRegistry()
app.state.provider_registry.register(comfyui_provider_set(app.state.comfyui_client, app.state.workflow_manager))
engine_config = json.loads((settings.workflows_dir.parent / "config" / "persona_engine.json").read_text(encoding="utf-8"))
app.state.llm_client = OllamaClient(
    base_url=settings.llm_api_url, model=settings.llm_model, timeout=settings.llm_timeout
)
app.state.generation_service = GenerationService(
    comfyui_client=app.state.comfyui_client,
    workflow_manager=app.state.workflow_manager,
    model_manager=app.state.model_manager,
    persona_manager=app.state.persona_manager,
    llm_client=app.state.llm_client,
    # Benchmark sem Chroma: sem a LoRA do Z-Image, a persona falha em vez de cair no Chroma.
    allow_chroma_fallback=bool(engine_config.get("chroma_fallback", False)),
)
app.state.scene_describer = SceneDescriber(
    comfyui_client=app.state.comfyui_client,
    workflow_manager=app.state.workflow_manager,
    llm_client=app.state.llm_client,
)
app.state.video_service = VideoService(
    comfyui_client=app.state.comfyui_client,
    llm_client=app.state.llm_client,
    scene_describer=app.state.scene_describer,
)
app.state.voice_service = VoiceService(
    comfyui_client=app.state.comfyui_client,
    persona_manager=app.state.persona_manager,
    llm_client=app.state.llm_client,
)
app.state.talk_service = TalkService(
    comfyui_client=app.state.comfyui_client,
    voice_service=app.state.voice_service,
    llm_client=app.state.llm_client,
    scene_describer=app.state.scene_describer,
)
app.state.swap_service = SwapService(
    comfyui_client=app.state.comfyui_client,
    llm_client=app.state.llm_client,
    generation_service=app.state.generation_service,
)
app.state.content_service = ContentService(
    llm_client=app.state.llm_client,
    persona_manager=app.state.persona_manager,
    comfyui_client=app.state.comfyui_client,
)
app.state.story_service = StoryService(
    llm_client=app.state.llm_client,
    persona_manager=app.state.persona_manager,
    comfyui_client=app.state.comfyui_client,
    workflow_manager=app.state.workflow_manager,
)
app.state.chat_service = ChatService(
    llm_client=app.state.llm_client,
    persona_manager=app.state.persona_manager,
    comfyui_client=app.state.comfyui_client,
)

config_path = settings.workflows_dir.parent / "config" / "default.json"
app.state.default_config = json.loads(config_path.read_text(encoding="utf-8"))

# --- Persona Engine V1: cena -> Face Lock -> validacao -> aceitar / tentar de novo
app.state.generation_history = GenerationHistory(settings.personas_dir)
app.state.generation_history.mark_interrupted()


async def _translate(text: str) -> str:
    return await to_english(app.state.llm_client, text)


_ocr = FlorenceTextReader(app.state.comfyui_client) if engine_config.get("trigger_leak_ocr_check") else None
app.state.generation_orchestrator = GenerationOrchestrator(
    personas=app.state.persona_repository,
    sheets=app.state.persona_sheets,
    providers=app.state.provider_registry,
    analyzer=ComfyImageAnalyzer(app.state.comfyui_client),
    validation=ValidationEngine([
        FaceIdentityValidator(), SubjectCountValidator(), AnatomyValidator(), PoseValidator(),
        BodyConsistencyValidator(), AgeValidator(), TriggerLeakValidator(_ocr),
    ]),
    retry=RetryPolicy(engine_config["retry"]["face_relock_before_regenerate"]),
    history=app.state.generation_history,
    prompt_builder=PromptBuilder(NegativePromptBuilder(engine_config["global_negative"])),
    cost=CostEstimator(RunPodProvider(settings.runpod_api_key, settings.runpod_pod_id),
                       engine_config["cost"].get("fallback_gpu_price_per_hour")),
    translator=_translate,
)


def _engine_notify(job: dict, started: float) -> None:
    best = next((r for r in job["results"] if r["id"] == job.get("best_result_id")), None)
    photo = notify.photo_link(best["image_url"]) if best and best.get("image_url") else None
    if job["status"] == "ACCEPTED":
        face = best.get("face_score") if best else None
        text = f"✅ Persona aprovada na tentativa {job['attempt']}" + (f" (rosto {face:.2f})" if face is not None else "")
    elif job["status"] == "FAILED":
        text = f"⚠️ {job['attempt']} tentativas reprovadas na validacao da persona"
    else:
        text, photo = f"⚠️ A geracao da persona falhou: {(job.get('error') or '')[:300]}", None
    notify.fire(text, started, photo=photo)


app.state.engine_notify = _engine_notify

repo_root = settings.workflows_dir.parent


async def _studio_busy() -> bool:
    return running_jobs() > 0 or await app.state.comfyui_client.is_busy()


sync_python = repo_root / ".venv-sync" / "bin" / "python"
app.state.idle_shutdown = IdleShutdownTracker(
    api_key=settings.runpod_api_key,
    pod_id=settings.runpod_pod_id,
    idle_minutes=settings.idle_shutdown_minutes,
    busy_check=_studio_busy,
    pre_stop_command=(
        [str(sync_python), str(repo_root / "scripts" / "volume_sync.py"), "all"] if sync_python.exists() else None
    ),
)

app.include_router(health.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(workflows.router, prefix="/api")
app.include_router(generate.router, prefix="/api")
app.include_router(video.router, prefix="/api")
app.include_router(voice.router, prefix="/api")
app.include_router(personas.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(content.router, prefix="/api")
app.include_router(story.router, prefix="/api")
app.include_router(persona_engine.router, prefix="/api")


@app.on_event("startup")
async def _start_idle_shutdown() -> None:
    app.state.idle_shutdown.start()


@app.get("/")
async def root():
    return {"service": "luna-ai-studio-backend", "status": "ok"}
