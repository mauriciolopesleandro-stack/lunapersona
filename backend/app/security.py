"""Protecao do backend do pod: quem souber a URL do proxy da RunPod chamava
a API (gerar, apagar referencia, gastar GPU). Agora toda alteracao (POST,
PUT, PATCH, DELETE) e todo o Persona Engine (/api/engine, inclusive leitura)
exigem o cabecalho X-Luna-Token com o token que a Vercel entrega so a quem
fez login.

Leituras do resto (GET) continuam abertas: fotos, audios e videos sao
carregados por <img>/<audio>, que nao mandam cabecalho - e os nomes sao
aleatorios.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

TOKEN_HEADER = "X-Luna-Token"
ENGINE_PREFIX = "/api/engine"
_OPEN_PATHS = {"/api/health"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def requires_token(method: str, path: str) -> bool:
    if method == "OPTIONS" or not path.startswith("/api/") or path in _OPEN_PATHS:
        return False
    return method not in _SAFE_METHODS or path.startswith(ENGINE_PREFIX)


def token_ok(expected: str, given: str | None) -> bool:
    return bool(given) and hmac.compare_digest(expected.encode(), (given or "").encode())


def token_middleware(expected: str):
    """Middleware HTTP. expected vazio = desligado (comportamento antigo)."""
    if not expected:
        log.warning("LUNA_API_TOKEN vazio: backend sem autenticacao.")

    async def middleware(request: Request, call_next):
        if expected and requires_token(request.method, request.url.path) \
                and not token_ok(expected, request.headers.get(TOKEN_HEADER)):
            return JSONResponse(status_code=401, content={"detail": "Acesso negado: entre de novo no site."})
        return await call_next(request)

    return middleware
