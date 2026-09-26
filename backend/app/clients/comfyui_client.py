"""Cliente isolado para falar com o ComfyUI via HTTP.

Nenhuma logica de negocio (workflows, modelos) vive aqui - apenas
comunicacao com a API do ComfyUI: conexao, envio de prompt, polling de
execucao e recuperacao de imagens.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx


class ComfyUIError(Exception):
    """Erro base para problemas de comunicacao com o ComfyUI."""


class ComfyUIConnectionError(ComfyUIError):
    """Nao foi possivel conectar ao ComfyUI (pod parado, URL errada, rede)."""


class ComfyUITimeoutError(ComfyUIError):
    """A geracao nao terminou dentro do tempo limite configurado."""


class ComfyUIExecutionError(ComfyUIError):
    """O ComfyUI aceitou o prompt mas a execucao falhou (erro de nó, etc.)."""


@dataclass
class ConnectionStatus:
    ok: bool
    message: str
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationOutputImage:
    filename: str
    subfolder: str
    type: str
    url: str


@dataclass
class GenerationResult:
    prompt_id: str
    images: list[GenerationOutputImage]
    duration_seconds: float
    raw_status: dict[str, Any]


class ComfyUIClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        connect_timeout: float = 10.0,
        generation_timeout: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.connect_timeout = connect_timeout
        self.generation_timeout = generation_timeout

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def check_connection(self) -> ConnectionStatus:
        """Verifica se o ComfyUI esta de pe e respondendo."""
        try:
            async with httpx.AsyncClient(timeout=self.connect_timeout) as client:
                resp = await client.get(
                    f"{self.base_url}/system_stats", headers=self._headers()
                )
        except httpx.RequestError as exc:
            return ConnectionStatus(
                ok=False,
                message=(
                    f"Falha de rede ao conectar em {self.base_url}: {exc}. "
                    "Verifique se o pod da RunPod esta rodando."
                ),
            )

        if resp.status_code == 404:
            return ConnectionStatus(
                ok=False,
                message=(
                    f"{self.base_url} respondeu 404 em /system_stats. "
                    "Isso normalmente indica que o pod esta parado ou o "
                    "servico ComfyUI nao esta escutando nessa porta."
                ),
            )
        if resp.status_code != 200:
            return ConnectionStatus(
                ok=False,
                message=f"ComfyUI respondeu status inesperado: {resp.status_code}",
            )

        try:
            stats = resp.json()
        except ValueError:
            stats = {}
        return ConnectionStatus(ok=True, message="ComfyUI conectado.", stats=stats)

    async def get_object_info(self) -> dict[str, Any]:
        """Retorna o catalogo de nos/parametros suportados pelo ComfyUI em execucao.

        Usado para validar workflows antes de envia-los (nomes de nos e
        valores de enum podem variar entre instalacoes/versoes).
        """
        try:
            async with httpx.AsyncClient(timeout=self.connect_timeout) as client:
                resp = await client.get(
                    f"{self.base_url}/object_info", headers=self._headers()
                )
        except httpx.RequestError as exc:
            raise ComfyUIConnectionError(str(exc)) from exc

        if resp.status_code != 200:
            raise ComfyUIConnectionError(
                f"/object_info retornou status {resp.status_code}"
            )
        return resp.json()

    async def list_loras(self) -> list[str]:
        """Nomes das LoRAs que o ComfyUI enxerga em models/loras."""
        try:
            async with httpx.AsyncClient(timeout=self.connect_timeout) as client:
                resp = await client.get(
                    f"{self.base_url}/object_info/LoraLoaderModelOnly", headers=self._headers()
                )
        except httpx.RequestError as exc:
            raise ComfyUIConnectionError(str(exc)) from exc

        if resp.status_code != 200:
            raise ComfyUIConnectionError(
                f"/object_info/LoraLoaderModelOnly retornou status {resp.status_code}"
            )
        node = resp.json().get("LoraLoaderModelOnly", {})
        options = node.get("input", {}).get("required", {}).get("lora_name", [[]])[0]
        return list(options) if isinstance(options, list) else []

    async def upload_image(self, filename: str, content: bytes) -> str:
        """Envia uma imagem para o ComfyUI (pasta input/) para uso em nos como
        LoadImage. Retorna o nome de arquivo real usado pelo ComfyUI (pode
        diferir do enviado se ja existir um arquivo com esse nome)."""
        try:
            async with httpx.AsyncClient(timeout=self.connect_timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/upload/image",
                    files={"image": (filename, content)},
                    data={"overwrite": "true"},
                    headers=self._headers(),
                )
        except httpx.RequestError as exc:
            raise ComfyUIConnectionError(str(exc)) from exc

        if resp.status_code != 200:
            raise ComfyUIExecutionError(
                f"Falha ao enviar imagem de referencia ao ComfyUI (status {resp.status_code}): {resp.text}"
            )
        data = resp.json()
        return data.get("name", filename)

    async def queue_prompt(self, graph: dict[str, Any], client_id: str | None = None) -> str:
        """Envia um grafo (formato de API do ComfyUI) para a fila de execucao."""
        client_id = client_id or str(uuid.uuid4())
        payload = {"prompt": graph, "client_id": client_id}

        try:
            async with httpx.AsyncClient(timeout=self.connect_timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/prompt", json=payload, headers=self._headers()
                )
        except httpx.RequestError as exc:
            raise ComfyUIConnectionError(str(exc)) from exc

        if resp.status_code != 200:
            raise ComfyUIExecutionError(
                f"ComfyUI recusou o prompt (status {resp.status_code}): {resp.text}"
            )

        data = resp.json()
        if "error" in data:
            raise ComfyUIExecutionError(f"Erro de validacao do workflow: {data['error']}")

        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyUIExecutionError(f"Resposta do ComfyUI sem prompt_id: {data}")
        return prompt_id

    async def wait_for_completion(self, prompt_id: str) -> dict[str, Any]:
        """Faz polling de /history/{prompt_id} ate a execucao terminar ou estourar timeout."""
        start = time.monotonic()
        poll_interval = 1.5

        async with httpx.AsyncClient(timeout=self.connect_timeout) as client:
            while True:
                elapsed = time.monotonic() - start
                if elapsed > self.generation_timeout:
                    raise ComfyUITimeoutError(
                        f"Geracao (prompt_id={prompt_id}) excedeu o timeout de "
                        f"{self.generation_timeout}s."
                    )

                try:
                    resp = await client.get(
                        f"{self.base_url}/history/{prompt_id}", headers=self._headers()
                    )
                except httpx.RequestError as exc:
                    raise ComfyUIConnectionError(str(exc)) from exc

                if resp.status_code == 200:
                    data = resp.json()
                    entry = data.get(prompt_id)
                    if entry:
                        status = entry.get("status", {})
                        if status.get("completed") is True:
                            return entry
                        if status.get("status_str") == "error":
                            raise ComfyUIExecutionError(
                                f"Execucao falhou para prompt_id={prompt_id}: "
                                f"{status.get('messages')}"
                            )

                await asyncio.sleep(poll_interval)

    def build_image_url(self, filename: str, subfolder: str, folder_type: str) -> str:
        from urllib.parse import urlencode

        query = urlencode({"filename": filename, "subfolder": subfolder, "type": folder_type})
        return f"{self.base_url}/view?{query}"

    def extract_images(self, history_entry: dict[str, Any]) -> list[GenerationOutputImage]:
        images: list[GenerationOutputImage] = []
        outputs = history_entry.get("outputs", {})
        for node_output in outputs.values():
            for img in node_output.get("images", []):
                filename = img.get("filename", "")
                subfolder = img.get("subfolder", "")
                folder_type = img.get("type", "output")
                images.append(
                    GenerationOutputImage(
                        filename=filename,
                        subfolder=subfolder,
                        type=folder_type,
                        url=self.build_image_url(filename, subfolder, folder_type),
                    )
                )
        return images
