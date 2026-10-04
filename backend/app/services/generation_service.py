"""Orquestra uma geracao de imagem: resolve modelo + workflow, monta o
grafo, envia ao ComfyUI, aguarda e retorna o resultado. E o unico lugar
onde ComfyUIClient, WorkflowManager e ModelManager se encontram.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.clients.comfyui_client import ComfyUIClient, ComfyUIError, GenerationOutputImage
from app.clients.llm_client import OllamaClient
from app.model_manager.manager import ModelManager
from app.persona_manager.manager import PersonaManager
from app.services.person_swap import (
    PersonSwapPlan,
    image_size,
    luna_faces,
    pack_correction,
    plan_person_swap,
    qwen_available,
    qwen_swap_params,
    swap_face_box,
    swap_similarity,
)
from app.services.prompt_translator import to_english
from app.services.reference_caption import clean_reference_caption
from app.services.scene_describer import describe_image
from app.workflow_manager.manager import WorkflowManager, WorkflowParamError


@dataclass
class GenerationRequest:
    prompt: str
    model_id: str
    workflow_id: str
    persona_id: str | None = None
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    guidance: float | None = None
    seed: int | None = None
    sampler_name: str | None = None
    scheduler: str | None = None
    # Foto enviada pela pessoa (ja no input/ do ComfyUI): a cena e mantida e a
    # pessoa vira a persona. denoise = quanto a foto e redesenhada.
    reference_image: str | None = None
    denoise: float | None = None
    # Pack com a persona: com reference_image, troca SO a pessoa da foto
    # (contorno recortado) e o resto volta identico. Sem isso, img2img da foto
    # inteira (o cenario tambem muda um pouco).
    person_swap: bool = False
    # Pack com Qwen: "recreate" = a foto inteira sai do Qwen (sem colagem, sem
    # emendas; o cenario pode mudar um pouco); "swap" = so a area dela volta.
    pack_mode: str = "swap"
    # Pack: retoque do rosto com o InstantID depois do Qwen.
    face_pass: bool = True
    # Historia: rosto de referencia do outro personagem ("nome [output]") e a
    # descricao dele (o rosto dele fica igual em todas as fotos).
    other_face_ref: str | None = None
    other_face_prompt: str = ""


# Vai no fim do prompt da persona. Sem palavras de enquadramento (close,
# poros visiveis): o corpo inteiro precisa continuar possivel.
REALISM_SUFFIX = (
    "candid amateur smartphone photo, unposed, real everyday place, background in focus, "
    "natural skin texture, subtle skin imperfections, natural light"
)
# Segunda passada (workflows com LoRA): amplia a imagem e redesenha os detalhes.
HIRES_SCALE = 1.5
HIRES_MAX_PIXELS = 2_400_000

# Se o pedido ja fala de expressao/olhar, a atitude padrao da persona nao entra.
_EXPRESSION_WORDS = re.compile(
    r"\b(?:smil\w*|laugh\w*|grin\w*|expression|surpris\w*|shock\w*|mouth|wink\w*|pout\w*|serious|sad|angry|"
    r"cry\w*|tongue|scream\w*|gaze|frown\w*|kiss\w*|looking)\b",
    re.I,
)
# Frases da descricao sobre outra pessoa (no pack so a mulher e redesenhada).
# ...mas frase que tambem fala dela fica (sem isso sumia a descricao dela e o
# modelo pintava fundo no lugar da cabeca).
_HER = re.compile(r"\b(?:she|her|woman|women|girl|lady|wife|girlfriend)\b", re.I)
_OTHER_PERSON = re.compile(r"\b(?:man|men|he|his|him|husband|boyfriend|guy|male|beard)\b", re.I)

# Correcao de rosto depois da geracao com LoRA (workflows/chroma-face-refine.json).
FACE_REFINE_WORKFLOW = "chroma-face-refine"

PERSON_SWAP_WORKFLOW = "chroma-person-swap-lora"
QWEN_SWAP_WORKFLOW = "qwen-person-swap"
# Pack com Qwen: ate 3 sementes enquanto o rosto dela no resultado nao parecer
# a persona (cosseno do ArcFace: a original da ~0.0-0.13, a persona ~0.5).
SWAP_ATTEMPTS = 3
SWAP_GOOD_SIM = 0.35
INSTANTID_FACE_WORKFLOW = "sdxl-instantid-face"


def _output_name(image: GenerationOutputImage) -> str:
    name = f"{image.subfolder}/{image.filename}" if image.subfolder else image.filename
    return f"{name} [output]"

IMG2IMG_WORKFLOW = "chroma-img2img"
IMG2IMG_LORA_WORKFLOW = "chroma-img2img-lora"


@dataclass
class GenerationResponse:
    prompt_id: str
    model_id: str
    workflow_id: str
    persona_id: str | None
    images: list[GenerationOutputImage]
    duration_seconds: float


class GenerationService:
    def __init__(
        self,
        comfyui_client: ComfyUIClient,
        workflow_manager: WorkflowManager,
        model_manager: ModelManager,
        persona_manager: PersonaManager,
        llm_client: OllamaClient | None = None,
    ) -> None:
        self.comfyui_client = comfyui_client
        self.workflow_manager = workflow_manager
        self.model_manager = model_manager
        self.persona_manager = persona_manager
        # Traduz o texto digitado (portugues) para ingles antes de gerar.
        self.llm_client = llm_client

    @staticmethod
    def _hires_size(width: int, height: int) -> tuple[int, int]:
        scale = min(HIRES_SCALE, (HIRES_MAX_PIXELS / (width * height)) ** 0.5)
        return round(width * scale / 16) * 16, round(height * scale / 16) * 16

    async def _lora_available(self, filename: str) -> bool:
        # Se o arquivo ainda nao chegou ao pod, a persona volta para o texto
        # de identidade em vez de a geracao falhar no ComfyUI.
        try:
            return filename in await self.comfyui_client.list_loras()
        except ComfyUIError:
            return False

    async def _describe_reference(self, image_name: str) -> str:
        """Descricao da foto de referencia (Florence-2). Sem o custom node no
        pod (ex.: o outro volume), a geracao segue so com o texto digitado."""
        return await describe_image(self.comfyui_client, self.workflow_manager, image_name)

    async def _refine_faces(
        self,
        images: list[GenerationOutputImage],
        params: dict[str, Any],
        face_box: tuple[int, int, int, int] | None = None,
    ) -> list[GenerationOutputImage]:
        """Redesenha o rosto em alta resolucao com a LoRA e cola de volta. Sem
        rosto na imagem (ou sem os nos no pod), fica a imagem original.
        face_box (x, y, largura, altura): no pack o rosto dela ja e conhecido -
        o Florence com "woman's face" pegava o rosto do homem e o deformava."""
        refined: list[GenerationOutputImage] = []
        for image in images:
            name = f"{image.subfolder}/{image.filename}" if image.subfolder else image.filename
            try:
                graph = self.workflow_manager.render(FACE_REFINE_WORKFLOW, {**params, "IMAGE": f"{name} [output]"})
                if face_box is not None:
                    x, y, w, h = face_box
                    graph["f0"] = {"class_type": "SolidMask", "inputs": {"value": 0, "width": params["WIDTH"], "height": params["HEIGHT"]}}
                    graph["f1"] = {"class_type": "SolidMask", "inputs": {"value": 1, "width": w, "height": h}}
                    graph["f2"] = {"class_type": "MaskComposite", "inputs": {"destination": ["f0", 0], "source": ["f1", 0], "x": x, "y": y, "operation": "add"}}
                    graph["23"]["inputs"]["mask"] = ["f2", 0]
                    graph.pop("22", None)
                    graph.pop("21", None)
                prompt_id = await self.comfyui_client.queue_prompt(graph)
                entry = await self.comfyui_client.wait_for_completion(prompt_id)
                refined.extend(self.comfyui_client.extract_images(entry) or [image])
            except (ComfyUIError, WorkflowParamError):
                refined.append(image)
        return refined

    def _render(self, workflow_id: str, params: dict[str, Any], recreate: bool = False) -> dict[str, Any]:
        """Grafo do workflow. recreate (pack "recriar a foto inteira"): salva a
        saida do Qwen inteira (no 40) em vez de colar so a area dela na foto
        original - sem emendas, mechas soltas nem cabelo misturado."""
        graph = self.workflow_manager.render(workflow_id, params)
        if recreate:
            graph["33"]["inputs"]["images"] = ["40", 0]
        if workflow_id == QWEN_SWAP_WORKFLOW and params.get("QWEN_LORA"):
            # LoRA da persona depois da Lightning (scripts/train_qwen_lora.sh)
            graph["7"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
                "model": ["6", 0], "lora_name": params["QWEN_LORA"], "strength_model": params["QWEN_LORA_STRENGTH"]}}
            graph["31"]["inputs"]["model"] = ["7", 0]
        return graph

    async def _instantid_face(
        self,
        image: GenerationOutputImage,
        persona_image: str,
        face_box: tuple[int, int, int, int],
        params: dict[str, Any],
    ) -> GenerationOutputImage | None:
        """Passada do InstantID no rosto dela (workflows/sdxl-instantid-face.json).
        Os pontos do rosto (angulo da cabeca) vem da foto original; sem rosto
        achado nela (olhando para baixo), vem da propria troca. None sem os
        modelos/nos no pod ou sem rosto nenhum."""
        x, y, w, h = face_box
        for kps_source in (params["REFERENCE_IMAGE"], _output_name(image)):
            try:
                graph = self.workflow_manager.render(INSTANTID_FACE_WORKFLOW, {
                    "IMAGE": _output_name(image), "PERSONA_IMAGE": persona_image, "ORIGINAL": kps_source,
                    "FACE_X": x, "FACE_Y": y, "FACE_W": w, "FACE_H": h,
                    "WIDTH": params["WIDTH"], "HEIGHT": params["HEIGHT"], "SEED": params["SEED"],
                })
                entry = await self.comfyui_client.wait_for_completion(await self.comfyui_client.queue_prompt(graph))
            except WorkflowParamError:
                return None
            except ComfyUIError:
                continue
            out = self.comfyui_client.extract_images(entry)
            return out[0] if out else None
        return None

    async def _other_face(
        self, image: GenerationOutputImage, req: GenerationRequest, size: tuple[int, int], seed: int
    ) -> GenerationOutputImage | None:
        """Historia: o rosto que nao e da persona (o mais parecido com ela e o
        dela) vira o rosto de referencia do outro personagem, pelo InstantID -
        so com a descricao em texto ele mudava de uma foto para a outra."""
        ref = self.persona_manager.get_primary_reference_bytes(req.persona_id)
        if not ref:
            return None
        persona_image = await self.comfyui_client.upload_image(*ref)
        name = _output_name(image)
        faces = await luna_faces(
            self.comfyui_client,
            {"1": {"class_type": "LoadImage", "inputs": {"image": name}},
             "2": {"class_type": "LoadImage", "inputs": {"image": persona_image}}},
            ["1", 0], ["2", 0],
        )
        if not faces or len(faces) < 2:
            return None
        persona_face = max(faces, key=lambda f: float(f.get("sim", 0.0)))
        others = [f for f in faces if f is not persona_face]
        x1, y1, x2, y2 = max(others, key=lambda f: (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1]))["bbox"]
        fw, fh = x2 - x1, y2 - y1
        x, y = max(0, int(x1 - fw * 0.15)), max(0, int(y1 - fh * 0.15))
        w, h = min(size[0] - x, int(fw * 1.3)), min(size[1] - y, int(fh * 1.25))
        try:
            graph = self.workflow_manager.render(INSTANTID_FACE_WORKFLOW, {
                "IMAGE": name, "ORIGINAL": name, "PERSONA_IMAGE": req.other_face_ref,
                "FACE_X": x, "FACE_Y": y, "FACE_W": w, "FACE_H": h, "WIDTH": size[0], "HEIGHT": size[1], "SEED": seed,
                "FACE_PROMPT": f"photo of {req.other_face_prompt or 'a man'}, natural skin texture, sharp focus",
                "NEGATIVE_PROMPT": "blurry, deformed face, asymmetric eyes, plastic skin, airbrushed, cgi, 3d render, "
                "cartoon, text, watermark",
            })
            entry = await self.comfyui_client.wait_for_completion(await self.comfyui_client.queue_prompt(graph))
        except (ComfyUIError, WorkflowParamError):
            return None
        out = self.comfyui_client.extract_images(entry)
        return out[0] if out else None

    async def _finish_swap(
        self,
        images: list[GenerationOutputImage],
        persona_image: str,
        plan: PersonSwapPlan,
        face_box: tuple[int, int, int, int] | None,
        params: dict[str, Any],
        face_pass: bool = True,
    ) -> tuple[list[GenerationOutputImage], float | None, bool]:
        """Rosto da persona pelo InstantID na troca do Qwen (identidade da foto
        dela, pontos do rosto da propria troca): a semelhanca subiu de ~0.55
        (correcao com a LoRA) para ~0.85. Fica a mais parecida - com o Qwen ja
        acertando (~0.9) o InstantID as vezes baixava um pouco.
        (imagens, semelhanca, se o InstantID rodou - ai a LoRA nao entra)."""
        sim = await swap_similarity(self.comfyui_client, _output_name(images[0]), persona_image, plan.face)
        if face_box is None or not face_pass:
            return images, sim, not face_pass
        iid = await self._instantid_face(images[0], persona_image, face_box, params)
        if iid is None:
            return images, sim, False
        iid_sim = await swap_similarity(self.comfyui_client, _output_name(iid), persona_image, plan.face)
        if sim is None or iid_sim is None or iid_sim >= sim:
            return [iid], iid_sim, True
        return images, sim, True

    async def generate(self, req: GenerationRequest) -> GenerationResponse:
        model = self.model_manager.get_model(req.model_id)

        seed = req.seed if req.seed is not None else uuid.uuid4().int % (2**32)

        user_prompt = await to_english(self.llm_client, req.prompt)
        prompt = user_prompt
        workflow_id = req.workflow_id
        reference_image_name: str | None = None
        lora_params: dict[str, Any] = {}
        use_lora = False
        attitude = ""
        face_params: dict[str, Any] = {}
        swap_plan: PersonSwapPlan | None = None
        persona_image_name: str | None = None
        persona_size: tuple[int, int] | None = None
        if req.persona_id:
            persona = self.persona_manager.get_persona(req.persona_id)
            lora = persona.lora
            if lora and lora.workflow_id in model.compatible_workflows and await self._lora_available(lora.file):
                # A LoRA ja carrega rosto, corpo e acessorios: o texto longo de
                # identidade so competiria com ela (e vira retrato/colagem).
                use_lora = True
                traits = persona.reference_prompt_fragment() if req.reference_image else persona.body_prompt_fragment()
                if req.person_swap:
                    # Cabelo da pessoa da foto (curto, cacheado) vencia: o
                    # formato do cabelo da persona entra junto.
                    hair = persona.identity.fixed.get("formato_cabelo", "").strip()
                    traits = ", ".join(p for p in (hair, traits) if p)
                # No pack a expressao e a da foto (a historia continua coerente).
                if not req.person_swap and not _EXPRESSION_WORDS.search(user_prompt):
                    attitude = persona.attitude_prompt_fragment()
                lead = ", ".join(p for p in (attitude, traits) if p)
                who = "a young woman, " if req.person_swap else ""
                prompt = f"photo of {lora.trigger}, {who}{lead + ', ' if lead else ''}{user_prompt}"
                face_params = {
                    "FACE_PROMPT": ", ".join(
                        p for p in (f"close-up portrait photo of {lora.trigger}", attitude, REALISM_SUFFIX) if p
                    ),
                    "LORA_NAME": lora.file,
                }
                workflow_id = lora.workflow_id
                lora_params = {"LORA_NAME": lora.file, "LORA_STRENGTH": lora.strength}
                # O site sempre manda o guidance das Configuracoes (4.0); o da
                # persona vence porque foi calibrado para a LoRA dela.
                if lora.guidance is not None:
                    lora_params["GUIDANCE"] = lora.guidance
            else:
                identity_fragment = persona.identity_prompt_fragment()
                if identity_fragment:
                    prompt = f"{identity_fragment}, {user_prompt}"

                # Se a persona tem uma foto de referencia, ancora a identidade
                # nela via FLUX Kontext (flux-kontext-reference) em vez de so
                # texto - forca esse workflow independente do que foi pedido,
                # pra toda geracao com essa persona ficar visualmente consistente.
                reference = self.persona_manager.get_primary_reference_bytes(req.persona_id)
                if not req.reference_image and reference and "flux-kontext-reference" in model.compatible_workflows:
                    filename, content = reference
                    reference_image_name = await self.comfyui_client.upload_image(filename, content)
                    workflow_id = "flux-kontext-reference"

        if req.reference_image:
            workflow_id = IMG2IMG_LORA_WORKFLOW if use_lora else IMG2IMG_WORKFLOW
            if req.person_swap:
                if not use_lora:
                    raise WorkflowParamError("Trocar a pessoa da foto precisa de uma persona com LoRA.")
                workflow_id = PERSON_SWAP_WORKFLOW
            if workflow_id not in model.compatible_workflows:
                raise WorkflowParamError(f"O modelo '{req.model_id}' nao aceita imagem de referencia.")
            reference_image_name = req.reference_image
            if req.person_swap:
                # Qwen-Image-Edit instalado: ele faz a troca (cena + foto da
                # persona + instrucao) - o Chroma redesenhando a regiao dela
                # perdia abraco, rosto de perfil e gestos. Sem ele, fica o Chroma.
                persona_ref = self.persona_manager.get_primary_reference_bytes(req.persona_id) if req.persona_id else None
                persona_size = image_size(persona_ref[1]) if persona_ref else None
                if persona_ref and persona_size and await qwen_available(self.comfyui_client):
                    persona_image_name = await self.comfyui_client.upload_image(*persona_ref)
                # Onde ela, o rosto e as maos dela estao e a descricao SO dela
                # (recorte): a da foto inteira falava do homem e ele aparecia
                # no lugar dela.
                swap_plan = await plan_person_swap(
                    self.comfyui_client,
                    req.reference_image,
                    req.width or model.defaults.get("width", 1024),
                    req.height or model.defaults.get("height", 1024),
                    persona_image_name,
                )
                description = swap_plan.caption
            else:
                # Com o "Quanto mudar" alto, so o que esta escrito sobrevive
                # da foto: descreve-la no prompt mantem roupa, pose e cenario.
                description = await self._describe_reference(req.reference_image)
            # Com persona, os tracos da pessoa original saem da descricao.
            if description and req.persona_id:
                description = clean_reference_caption(description, keep_expression=not attitude)
            if description and req.person_swap:
                # O recorte as vezes pega um pedaco do outro: frase so sobre
                # ele sai (a que fala dela tambem fica).
                description = " ".join(
                    s
                    for s in re.split(r"(?<=[.!?])\s+", description)
                    if not _OTHER_PERSON.search(s) or _HER.search(s)
                )
            if description:
                prompt = f"{prompt}, {description}"

        if use_lora:
            prompt = f"{prompt}, {REALISM_SUFFIX}"

        params: dict[str, Any] = {
            "PROMPT": prompt,
            "WIDTH": req.width or model.defaults.get("width", 1024),
            "HEIGHT": req.height or model.defaults.get("height", 1024),
            "STEPS": req.steps or model.defaults.get("steps", 20),
            "GUIDANCE": req.guidance or model.defaults.get("guidance", 2.5),
            "SEED": seed,
            "SAMPLER_NAME": req.sampler_name or model.defaults.get("sampler_name", "euler"),
            "SCHEDULER": req.scheduler or model.defaults.get("scheduler", "simple"),
            "FILENAME_PREFIX": "luna_studio",
        }
        if reference_image_name:
            params["REFERENCE_IMAGE"] = reference_image_name
        if swap_plan is not None:
            params["POINTS_POS"], params["POINTS_NEG"] = swap_plan.points_pos, swap_plan.points_neg
            params["HEAD_X"], params["HEAD_Y"], params["HEAD_W"], params["HEAD_H"] = swap_plan.head
            if persona_image_name and persona_size:
                workflow_id = QWEN_SWAP_WORKFLOW
                params["PERSONA_IMAGE"] = persona_image_name
                # A correcao escrita no "refazer" da foto entra na instrucao do
                # Qwen (antes a instrucao era montada so pela foto e ela sumia).
                qwen_lora = persona.lora if persona.lora and persona.lora.qwen_file else None
                if qwen_lora and not await self._lora_available(qwen_lora.qwen_file):
                    qwen_lora = None
                params.update(qwen_swap_params(
                    swap_plan, params["WIDTH"], params["HEIGHT"], persona_size, pack_correction(user_prompt),
                    recreate=req.pack_mode == "recreate", lora=qwen_lora is not None,
                ))
                if qwen_lora is not None:
                    params["QWEN_LORA"], params["QWEN_LORA_STRENGTH"] = qwen_lora.qwen_file, qwen_lora.qwen_strength
        if req.denoise is not None:
            params["DENOISE"] = req.denoise
        if use_lora:
            params["HIRES_WIDTH"], params["HIRES_HEIGHT"] = self._hires_size(params["WIDTH"], params["HEIGHT"])
        params.update(lora_params)
        params.update(self.model_manager.loader_params(req.model_id))

        recreate = workflow_id == QWEN_SWAP_WORKFLOW and req.pack_mode == "recreate"
        graph = self._render(workflow_id, params, recreate)

        start = time.monotonic()
        prompt_id = await self.comfyui_client.queue_prompt(graph)
        history_entry = await self.comfyui_client.wait_for_completion(prompt_id)

        images = self.comfyui_client.extract_images(history_entry)
        face_box = swap_face_box(swap_plan, params["WIDTH"], params["HEIGHT"]) if swap_plan is not None else None
        # Pack sem o rosto dela achado (de costas): nao corrige rosto nenhum -
        # o unico rosto que sobra e o do outro.
        skip_refine = swap_plan is not None and face_box is None
        if workflow_id == QWEN_SWAP_WORKFLOW and images and persona_image_name and swap_plan is not None:
            # Confere se a persona "pegou" (rosto dela no resultado FINAL, ja com
            # o InstantID, parecido com a foto da persona) e, se nao, tenta outras
            # sementes - fica a melhor. Conferir antes do InstantID refazia a
            # troca a toa (ele sozinho ja levava 0.25 para 0.75).
            best_images, best_sim, used_iid = await self._finish_swap(images, persona_image_name, swap_plan, face_box, params, req.face_pass)
            for attempt in range(1, SWAP_ATTEMPTS):
                if best_sim is None or best_sim >= SWAP_GOOD_SIM:
                    break
                retry = self._render(workflow_id, {**params, "SEED": (seed + attempt * 7919) % (2**32)}, recreate)
                retry_id = await self.comfyui_client.queue_prompt(retry)
                retry_images = self.comfyui_client.extract_images(await self.comfyui_client.wait_for_completion(retry_id))
                if not retry_images:
                    continue
                cand_images, sim, cand_iid = await self._finish_swap(retry_images, persona_image_name, swap_plan, face_box, params, req.face_pass)
                if sim is not None and sim > best_sim:
                    best_images, best_sim, used_iid, prompt_id = cand_images, sim, cand_iid, retry_id
            images = best_images
            skip_refine = skip_refine or used_iid
        if face_params and images and not skip_refine and FACE_REFINE_WORKFLOW in model.compatible_workflows:
            images = await self._refine_faces(images, {**params, **face_params}, face_box)
        if req.other_face_ref and images and req.persona_id:
            size = (params.get("HIRES_WIDTH") or params["WIDTH"], params.get("HIRES_HEIGHT") or params["HEIGHT"])
            other = await self._other_face(images[0], req, size, params["SEED"])
            if other is not None:
                images = [other]
        duration = time.monotonic() - start

        return GenerationResponse(
            prompt_id=prompt_id,
            model_id=req.model_id,
            workflow_id=workflow_id,
            persona_id=req.persona_id,
            images=images,
            duration_seconds=duration,
        )
