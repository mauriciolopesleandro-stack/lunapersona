"""Servidor E2E LOCAL da tela Persona V2: site compilado (frontend/dist) + rotas /api/v2 REAIS + servico
REAL, com a GPU MOCKADA (FakeFactory dos testes). Nao fala com RunPod, ComfyUI nem nada externo:
/api/session e /api/runpod-* respondem fixo daqui.

  cd backend && .venv/Scripts/python.exe -m tests.e2e.server_v2   (porta 8077)
"""
import hashlib
import io
import json
import shutil
import tempfile
from pathlib import Path

import uvicorn
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

from tests.conftest import FAKE_MASTERS, REPO, SHEET
from tests.test_engines_api import TOKEN, make
from tests.test_persona_transfer import wide_tattoo_photo

PORT = 8077


def personas_dir(root: Path) -> Path:
    """Mesmo preparo da fixture engine_dir: ficha real da Luna com masters de mentira."""
    target = root / "personas" / "luna"
    (target / "references").mkdir(parents=True)
    shutil.copy(REPO / "personas" / "luna" / "persona.json", target / "persona.json")
    data = json.loads(SHEET.read_text(encoding="utf-8"))
    for role, content in FAKE_MASTERS.items():
        master = data["master_references"][role]
        (target / master["file"]).parent.mkdir(parents=True, exist_ok=True)
        (target / master["file"]).write_bytes(content)
        master["sha256"] = hashlib.sha256(content).hexdigest()
    (target / "persona_sheet.json").write_text(json.dumps(data), encoding="utf-8")
    return root / "personas"


def build():
    root = Path(tempfile.mkdtemp(prefix="v2e2e_"))
    app, factory = make(personas_dir(root), root)
    # foto de teste que os fakes entendem (mesma geometria do Reader/Seg mockados)
    Image.fromarray(wide_tattoo_photo()[0]).save(root / "foto_teste_e2e.png")
    print("foto de teste:", root / "foto_teste_e2e.png", flush=True)

    @app.get("/e2e/foto.png")
    async def foto():
        return FileResponse(root / "foto_teste_e2e.png")

    @app.get("/api/session")
    async def session():
        return {"authenticated": True}

    @app.get("/api/runpod-status")
    async def status(request: Request):
        base = f"{request.base_url}api"  # mesma origem da pagina (sem CORS)
        return {"running": True, "backendReady": True, "desiredStatus": "RUNNING", "podId": "e2e", "dataCenterId": "local",
                "gpu": "GPU MOCKADA", "apiBase": base, "apiToken": TOKEN, "costPerHr": 0, "uptimeSeconds": 1,
                "liveSpend": 0, "balance": 0}

    @app.get("/api/personas")
    async def personas():
        return {"personas": [{"id": "luna", "name": "Luna", "description": "", "reference_count": 4}]}

    @app.get("/api/health")
    async def health():
        return {"backend": "ok", "comfyui": {"ok": True, "message": "mock", "stats": {}}}

    @app.get("/api/models")
    async def models():
        return {"models": []}

    @app.get("/api/workflows")
    async def workflows():
        return {"workflows": []}

    @app.get("/view/{loc}")
    async def view(loc: str):
        px = factory.store.images.get(loc)
        if px is None:
            return JSONResponse({"detail": "nao existe"}, 404)
        buf = io.BytesIO()
        Image.fromarray(px).save(buf, "PNG")
        return Response(buf.getvalue(), media_type="image/png")

    dist = REPO / "frontend" / "dist"
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        f = dist / path
        return FileResponse(f if path and f.is_file() else dist / "index.html")

    return app


if __name__ == "__main__":
    uvicorn.run(build(), host="127.0.0.1", port=PORT, log_level="warning")
