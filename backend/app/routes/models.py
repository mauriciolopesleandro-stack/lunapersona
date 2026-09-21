from dataclasses import asdict

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/models")
async def list_models(request: Request):
    model_manager = request.app.state.model_manager
    return {"models": [asdict(m) for m in model_manager.list_models()]}
