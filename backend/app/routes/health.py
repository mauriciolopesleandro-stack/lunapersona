from fastapi import APIRouter, Request

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
