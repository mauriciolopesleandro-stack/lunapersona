"""Servico das engines V2 (Replacement e Face Swap) para as rotas /api/v2.

So orquestra: valida o pedido ANTES de gastar GPU (modo, modelo, opcoes), le a master da Persona
Sheet, monta os negativos da V1, dispara o job e aplica a retencao. Quem fala com o ComfyUI e a
fabrica injetada (providers/comfyui/engines_v2.py); a Geracao continua nas rotas da V1.
"""
from __future__ import annotations

import io
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from PIL import Image

from app.core.engines.attributes import catalog as attribute_catalog
from app.core.engines.face_swap import MODES as FACE_SWAP_MODES
from app.core.engines.face_swap import FULL_PERSON, FaceSwapRequest
from app.core.engines.models import AUTO, ModelRegistry
from app.core.engines.policies import LADDER, POLICIES
from app.core.engines.replacement import ReplacementRequest
from app.providers.base import ReferenceImage

log = logging.getLogger(__name__)

ENGINES = ("generation", "replacement", "face_swap")
REQUEST_FIELDS = ("replacement_version", "pose_required", "clothing_required", "accessories_required", "remove_tattoos",
                  "quality_profile", "identity_strength", "pose_strength", "depth_strength", "debug", "qwen_face_lock")
MODES = ("FAST", "QUALITY", "MAX_QUALITY")


class EngineRequestError(ValueError):
    pass


class EngineFactory(Protocol):
    async def replacement(self, model: Any, qwen: bool | None = None, version: str = "v2"): ...

    async def face_swap(self, model: Any): ...


@dataclass(frozen=True)
class RetentionRule:
    prefix: str
    max_age_hours: float


class RetentionSweeper:
    """Apaga do diretorio de entrada do ComfyUI SO os arquivos das engines V2 (prefixos conhecidos)
    mais velhos que a regra. A primeira regra cujo prefixo casa decide; arquivo sem regra fica."""

    def __init__(self, directory: Path | None, rules: list[RetentionRule]) -> None:
        self.directory = directory
        self.rules = rules

    def rule_for(self, name: str) -> RetentionRule | None:
        return next((r for r in self.rules if name.startswith(r.prefix)), None)

    def sweep(self, now: float | None = None) -> list[str]:
        if self.directory is None or not self.directory.is_dir():
            return []
        now = time.time() if now is None else now
        removed = []
        for path in self.directory.iterdir():
            rule = self.rule_for(path.name)
            if rule is None or not path.is_file():
                continue
            if now - path.stat().st_mtime > rule.max_age_hours * 3600:
                try:
                    path.unlink()
                    removed.append(path.name)
                except OSError:
                    log.warning("retencao: nao consegui apagar %s", path.name)
        return removed


def load_engines_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class EnginesV2Service:
    def __init__(self, *, config: dict[str, Any], registry: ModelRegistry, sheets, negative_builder,
                 factory: EngineFactory, upload: Callable[[str, bytes], Awaitable[str]],
                 url_for: Callable[[str], str], jobs, sweeper: RetentionSweeper | None = None,
                 telemetry_log: Path | None = None) -> None:
        self.cfg = config
        self.registry = registry
        self.sheets = sheets
        self.negative_builder = negative_builder
        self.factory = factory
        self.upload_fn = upload
        self.url_for = url_for
        self.jobs = jobs
        self.sweeper = sweeper
        self.telemetry_log = telemetry_log

    # --- catalogo para a tela -----------------------------------------------------------------------

    def catalog(self) -> dict[str, Any]:
        models = [{"id": AUTO, "title": "Auto", "status": "AVAILABLE", "default": True}]
        for m in self.registry.checkpoints():
            models.append({"id": m.id, "title": m.title, "status": m.status, "license": m.license,
                           "license_status": m.license_status, "commercial_use": m.commercial_use,
                           "download_auth": m.download_auth, "default": False})
        return {
            "engines": list(ENGINES), "modes": list(MODES), "models": models,
            "replacement": {"options": list(ReplacementRequest.OPTIONS), "advanced": list(ReplacementRequest.ADVANCED),
                            "defaults": {m: POLICIES[m].to_dict() for m in MODES},
                            "attributes": attribute_catalog()},
            "face_swap": {"modes": list(FACE_SWAP_MODES), "head_backends": ["sdxl", "qwen_bfs"]},
            "processing": self.cfg.get("processing", {}),
            "retention": {k: v for k, v in self.cfg.get("retention", {}).items() if k != "comfyui_input_dir"},
        }

    def licenses(self) -> dict[str, Any]:
        return {"registry_version": self.registry.version, "models": self.registry.license_report()}

    # --- entrada -----------------------------------------------------------------------------------

    async def upload(self, filename: str, content: bytes) -> str:
        up = self.cfg.get("uploads", {})
        if len(content) > float(up.get("max_mb", 20)) * 1024 * 1024:
            raise EngineRequestError(f"imagem maior que {up.get('max_mb', 20)} MB")
        try:
            img = Image.open(io.BytesIO(content))
            img = img.convert("RGB")
        except Exception as exc:  # noqa: BLE001 - qualquer falha de leitura = arquivo invalido
            raise EngineRequestError("arquivo nao e uma imagem valida") from exc
        side = int(up.get("max_side", 1600))
        img.thumbnail((side, side), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        stem = "".join(c for c in Path(filename or "foto").stem if c.isalnum() or c in "-_")[:30] or "foto"
        return await self.upload_fn(f"{up.get('prefix', 'v2in_')}{stem}_{int(time.time())}.png", buf.getvalue())

    def _processing(self, processing: str) -> None:
        allowed = self.cfg.get("processing", {}).get("allowed", ["local"])
        if processing not in allowed:
            raise EngineRequestError(f"processamento '{processing}' nao suportado (so {allowed})")

    def persona_attributes(self, persona_id: str) -> dict[str, Any]:
        """Politica da Persona Sheet resolvida (para a tela mostrar o padrao da persona)."""
        from app.core.engines.attributes import resolve

        try:
            return resolve(self.sheets.get(persona_id).data).to_dict()
        except Exception as exc:  # noqa: BLE001
            raise EngineRequestError(f"persona '{persona_id}': {exc}") from exc

    def _persona(self, persona_id: str) -> tuple[ReferenceImage, str]:
        try:
            sheet = self.sheets.get(persona_id)
            m = sheet.master("master_face")
            master = ReferenceImage(m.reference_id, m.file, sheet.read_master("master_face"), m.sha256)
        except Exception as exc:  # noqa: BLE001 - persona sem ficha/master: erro claro, nada roda
            raise EngineRequestError(f"persona '{persona_id}' sem Persona Sheet/master_face: {exc}") from exc
        negative = ", ".join(self.negative_builder.build(sheet, single_subject=True).all_terms())
        return master, negative

    async def _master_body(self, persona_id: str):
        try:
            sheet = self.sheets.get(persona_id)
            m = sheet.master("master_body")
            return ReferenceImage(m.reference_id, m.file, sheet.read_master("master_body"), m.sha256)
        except Exception:  # noqa: BLE001 - sem master_body: proporcoes ficam NAO COMPARAVEIS
            return None

    def _model(self, model: str):
        from app.core.engines.models import ModelUnavailableError

        try:
            return self.registry.select_checkpoint(model)
        except ModelUnavailableError as exc:
            raise EngineRequestError(str(exc)) from exc

    # --- jobs --------------------------------------------------------------------------------------

    def _finish(self, engine: str, out, extra: dict[str, Any]) -> dict[str, Any]:
        d = out.to_dict()
        d["image_url"] = self.url_for(out.image)
        d["intermediates"] = {k: self.url_for(v) for k, v in (d.get("intermediates") or {}).items()}
        d.update(engine=engine, processing="local", **extra)
        if self.telemetry_log is not None:
            try:
                self.telemetry_log.parent.mkdir(parents=True, exist_ok=True)
                with self.telemetry_log.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"ts": time.time(), **d["telemetry"], "status": d["status"]}, default=str) + "\n")
            except OSError:
                log.warning("telemetria V2: nao consegui gravar o log")
        if self.sweeper is not None:
            self.sweeper.sweep()
        return d

    def start_replacement(self, *, image: str, persona_id: str, mode: str = "QUALITY", model: str = AUTO,
                          seed: int = 7801, options: dict[str, bool] | None = None,
                          advanced: dict[str, Any] | None = None, keep_intermediates: bool | None = None,
                          processing: str = "local", preserve_attributes: list[str] | None = None,
                          remove_attributes: list[str] | None = None,
                          reconstruct_attributes: list[str] | None = None,
                          structured_policy: dict[str, Any] | None = None,
                          request_fields: dict[str, Any] | None = None) -> dict[str, Any]:
        self._processing(processing)
        fields = dict(request_fields or {})
        bad = [k for k in fields if k not in REQUEST_FIELDS]
        if bad:
            raise EngineRequestError(f"campos desconhecidos no pedido: {bad}")
        if fields.get("replacement_version", "v2") == "v1":
            raise EngineRequestError("a Persona Replacement V1 continua disponivel so pelo script (scripts/replacement_teste1.py); "
                                     "a rota /api/v2/replace roda a V2")
        if mode not in POLICIES and mode not in LADDER:
            raise EngineRequestError(f"modo invalido: {mode}")
        if "lora_strength" in (advanced or {}):
            raise EngineRequestError("a forca da LoRA nao e ajustavel por pedido (fica a do registro)")
        chosen = self._model(model)
        master, negative = self._persona(persona_id)
        keep = self.cfg.get("retention", {}).get("keep_intermediates_default", False) if keep_intermediates is None else keep_intermediates
        req = ReplacementRequest(image=image, persona_id=persona_id, master=master, mode=mode, model=chosen.id, seed=seed,
                                 options=dict(options or {}), advanced=dict(advanced or {}), negative=negative,
                                 keep_intermediates=bool(keep), preserve_attributes=list(preserve_attributes or []),
                                 remove_attributes=list(remove_attributes or []),
                                 reconstruct_attributes=list(reconstruct_attributes or []),
                                 structured_policy=structured_policy or None,
                                 persona_sheet=self.sheets.get(persona_id).data, **fields)
        try:
            req.validate()
            req.plan()  # resolve a politica de atributos: conflito/atributo invalido = 400 antes da GPU
        except ValueError as exc:
            raise EngineRequestError(str(exc)) from exc

        async def work() -> dict[str, Any]:
            engine = await self.factory.replacement(chosen, qwen=req.qwen_face_lock, version=req.replacement_version)
            engine.master_body_loader = self._master_body  # proporcoes da Persona (so leitura)
            out = await engine.run(req)
            return self._finish("replacement", out, {"model": chosen.id, "mode": mode})

        return self.jobs.start(work)

    def start_face_swap(self, *, image: str, persona_id: str, mode: str = "FACE_INTEGRATED", model: str = AUTO,
                        seed: int = 7901, head_backend: str = "sdxl", reference_strength: float = 0.55,
                        replacement_mode: str = "QUALITY", processing: str = "local") -> dict[str, Any]:
        self._processing(processing)
        if mode not in FACE_SWAP_MODES:
            raise EngineRequestError(f"modo de troca de rosto invalido: {mode}")
        if head_backend not in ("sdxl", "qwen_bfs"):
            raise EngineRequestError(f"head_backend invalido: {head_backend}")
        if not 0.0 <= reference_strength <= 0.6:
            raise EngineRequestError("reference_strength fora de 0..0.6")
        chosen = self._model(model)
        master, negative = self._persona(persona_id)
        rep = None
        if mode == FULL_PERSON:  # so quando o usuario escolhe FULL_PERSON explicitamente
            rep = ReplacementRequest(image=image, persona_id=persona_id, master=master, mode=replacement_mode,
                                     model=chosen.id, seed=seed, negative=negative)
        req = FaceSwapRequest(image=image, persona_id=persona_id, master=master, mode=mode, seed=seed,
                              head_backend=head_backend, reference_strength=reference_strength, negative=negative,
                              replacement=rep)

        async def work() -> dict[str, Any]:
            engine = await self.factory.face_swap(chosen)
            out = await engine.run(req)
            return self._finish("face_swap", out, {"model": chosen.id, "mode": mode})

        return self.jobs.start(work)


__all__ = ["ENGINES", "MODES", "EngineRequestError", "EnginesV2Service", "RetentionRule", "RetentionSweeper",
           "load_engines_config"]
