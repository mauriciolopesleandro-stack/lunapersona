from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.persona import PersonaRepository
from app.core.persona.references import ReferenceManager
from app.persona_manager.manager import PersonaManager
from app.routes import persona_engine
from app.security import TOKEN_HEADER, token_middleware

TOKEN = "token-de-teste"


def png(width: int = 512, height: int = 512, tag: bytes = b"") -> bytes:
    """So o cabecalho de um PNG (o backend le o tamanho por ele) + bytes
    para cada arquivo ser diferente."""
    return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00" + tag


def engine_app(personas_dir: Path, **state) -> FastAPI:
    app = FastAPI()
    app.middleware("http")(token_middleware(TOKEN))
    manager = PersonaManager(personas_dir)
    app.state.persona_manager = manager
    app.state.persona_repository = PersonaRepository(personas_dir)
    app.state.reference_manager = ReferenceManager(manager)
    for key, value in state.items():
        setattr(app.state, key, value)
    app.include_router(persona_engine.router, prefix="/api")
    return app


def client(app: FastAPI, authorized: bool = True) -> TestClient:
    return TestClient(app, headers={TOKEN_HEADER: TOKEN} if authorized else {})
