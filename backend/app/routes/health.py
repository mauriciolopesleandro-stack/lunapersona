from fastapi import APIRouter, Request

from app.jobs import running_jobs

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    comfyui_client = request.app.state.comfyui_client
    status = await comfyui_client.check_connection()
    return {
        "backend": "ok",
        "comfyui": {
            "ok": status.ok,
            "message": status.message,
            "stats": status.stats,
        },
    }


@router.get("/busy")
async def busy(request: Request):
    """Algo em andamento? Usado antes de reiniciar o backend (atualizacao):
    so a fila do ComfyUI nao basta - a geracao passa antes por upload,
    descricao da foto e traducao, fora da fila. Conta como uso: o script de
    atualizacao fica perguntando enquanto espera e o pod desligava no meio."""
    request.app.state.idle_shutdown.touch()
    jobs = running_jobs()
    comfy = await request.app.state.comfyui_client.is_busy()
    return {"busy": bool(jobs) or comfy, "jobs": jobs, "comfyui": comfy}
