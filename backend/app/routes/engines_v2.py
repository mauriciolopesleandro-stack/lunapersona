"""Rotas das engines V2 (/api/v2/...): Replacement e Face Swap. A Geracao continua em /api/engine
(V1, sem mudanca). Tudo exige o X-Luna-Token (app/security.py), como as outras rotas.

  GET  /api/v2/engines          catalogo para a tela (engines, modos, modelos, opcoes, retencao)
  GET  /api/v2/models/licenses  registro de modelos com licenca, uso comercial, origem e hash
  GET  /api/v2/personas/{id}/attributes  politica de atributos da persona (spec 45)
  POST /api/v2/replace          foto + persona (+ preserve/remove/reconstruct_attributes) -> job (202)
  POST /api/v2/faceswap         foto + persona + modo -> job (202)
  GET  /api/v2/jobs/{job_id}    estado/resultado (imagem, validacao por dimensao, telemetria)
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.core.engines.service import EngineRequestError

router = APIRouter(prefix="/v2")


def _svc(request: Request):
    svc = getattr(request.app.state, "engines_v2", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="engines V2 desligadas neste backend")
    return svc


def _touch(request: Request) -> None:
    tracker = getattr(request.app.state, "idle_shutdown", None)
    if tracker is not None:
        tracker.touch()


def _list(text: str, what: str) -> list[str]:
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = [t for t in text.split(",")]
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise HTTPException(status_code=400, detail=f"{what}: precisa ser uma lista de nomes")
    return [x.strip() for x in data if x.strip()]


def _json(text: str, what: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"{what}: JSON invalido") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail=f"{what}: precisa ser um objeto")
    return data


async def _image(request: Request, file: UploadFile | None, image: str) -> str:
    if file is not None:
        try:
            return await _svc(request).upload(file.filename or "", await file.read())
        except EngineRequestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if image:
        return image
    raise HTTPException(status_code=400, detail="envie a foto (file) ou o nome ja enviado (image)")


@router.get("/engines")
async def engines(request: Request):
    return _svc(request).catalog()


@router.get("/models/licenses")
async def licenses(request: Request):
    return _svc(request).licenses()


@router.get("/personas/{persona_id}/attributes")
async def persona_attributes(persona_id: str, request: Request):
    """Politica de atributos padrao da persona (Persona Sheet), com a origem de cada uma."""
    try:
        return _svc(request).persona_attributes(persona_id)
    except EngineRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/replace", status_code=202)
async def replace(
    request: Request,
    persona_id: str = Form(...),
    file: UploadFile | None = File(None),
    image: str = Form(""),
    mode: str = Form("QUALITY"),
    model: str = Form("auto"),
    seed: int = Form(7801),
    options: str = Form(""),
    advanced: str = Form(""),
    keep_intermediates: bool | None = Form(None),
    processing: str = Form("local"),
    preserve_attributes: str = Form(""),
    remove_attributes: str = Form(""),
    reconstruct_attributes: str = Form(""),
    policy: str = Form(""),
):
    _touch(request)
    opts, adv = _json(options, "options"), _json(advanced, "advanced")
    attrs = {"preserve_attributes": _list(preserve_attributes, "preserve_attributes"),
             "remove_attributes": _list(remove_attributes, "remove_attributes"),
             "reconstruct_attributes": _list(reconstruct_attributes, "reconstruct_attributes"),
             # spec 46.10: politica estruturada ({"preserve": {"accessories": [...]}, "remove": {"markings": [...]}})
             "structured_policy": _json(policy, "policy")}
    locator = await _image(request, file, image)
    try:
        return _svc(request).start_replacement(image=locator, persona_id=persona_id, mode=mode, model=model, seed=seed,
                                               options=opts, advanced=adv, keep_intermediates=keep_intermediates,
                                               processing=processing, **attrs)
    except EngineRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/faceswap", status_code=202)
async def faceswap(
    request: Request,
    persona_id: str = Form(...),
    file: UploadFile | None = File(None),
    image: str = Form(""),
    mode: str = Form("FACE_INTEGRATED"),
    model: str = Form("auto"),
    seed: int = Form(7901),
    head_backend: str = Form("sdxl"),
    reference_strength: float = Form(0.55),
    replacement_mode: str = Form("QUALITY"),
    processing: str = Form("local"),
):
    _touch(request)
    locator = await _image(request, file, image)
    try:
        return _svc(request).start_face_swap(image=locator, persona_id=persona_id, mode=mode, model=model, seed=seed,
                                             head_backend=head_backend, reference_strength=reference_strength,
                                             replacement_mode=replacement_mode, processing=processing)
    except EngineRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs/{job_id}")
async def job(job_id: str, request: Request):
    _touch(request)
    return _svc(request).jobs.get(job_id)
