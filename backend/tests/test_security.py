import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security import TOKEN_HEADER, requires_token, token_middleware


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/api/health", False),
        ("GET", "/api/personas", False),
        ("POST", "/api/generate/jobs", True),
        ("DELETE", "/api/personas/luna/references/x", True),
        ("GET", "/api/engine/personas", True),
        ("OPTIONS", "/api/engine/personas", False),
        ("GET", "/api/v2/jobs/abc", True),
        ("GET", "/api/v2/engines", True),
        ("POST", "/api/v2/replace", True),
        ("GET", "/", False),
    ],
)
def test_requires_token(method, path, expected):
    assert requires_token(method, path) is expected


def _app(token: str) -> TestClient:
    app = FastAPI()
    app.middleware("http")(token_middleware(token))

    @app.post("/api/generate")
    async def generate():
        return {"ok": True}

    @app.get("/api/engine/personas")
    async def personas():
        return {"ok": True}

    return TestClient(app)


def test_without_token_is_denied():
    client = _app("segredo")
    assert client.post("/api/generate").status_code == 401
    assert client.get("/api/engine/personas").status_code == 401
    assert client.get("/api/engine/personas", headers={TOKEN_HEADER: "errado"}).status_code == 401


def test_with_token_is_allowed():
    client = _app("segredo")
    assert client.post("/api/generate", headers={TOKEN_HEADER: "segredo"}).status_code == 200


def test_no_token_configured_keeps_old_behaviour():
    assert _app("").post("/api/generate").status_code == 200
