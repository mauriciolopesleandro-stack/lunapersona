import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.clients.comfyui_client import ComfyUIClient
from app.config import get_settings
from app.idle_shutdown import IdleShutdownTracker
from app.model_manager.manager import ModelManager
from app.persona_manager.manager import PersonaManager
from app.routes import generate, health, models, personas, workflows
from app.services.generation_service import GenerationService
from app.workflow_manager.manager import WorkflowManager

settings = get_settings()

app = FastAPI(title="Luna AI Studio - Backend", version="0.1.0")

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
app.state.generation_service = GenerationService(
    comfyui_client=app.state.comfyui_client,
    workflow_manager=app.state.workflow_manager,
    model_manager=app.state.model_manager,
    persona_manager=app.state.persona_manager,
)

config_path = settings.workflows_dir.parent / "config" / "default.json"
app.state.default_config = json.loads(config_path.read_text(encoding="utf-8"))

app.state.idle_shutdown = IdleShutdownTracker(
    api_key=settings.runpod_api_key,
    pod_id=settings.runpod_pod_id,
    idle_minutes=settings.idle_shutdown_minutes,
)

app.include_router(health.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(workflows.router, prefix="/api")
app.include_router(generate.router, prefix="/api")
app.include_router(personas.router, prefix="/api")


@app.on_event("startup")
async def _start_idle_shutdown() -> None:
    app.state.idle_shutdown.start()


@app.get("/")
async def root():
    return {"service": "luna-ai-studio-backend", "status": "ok"}
