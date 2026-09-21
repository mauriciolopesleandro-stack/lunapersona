# /workflows

Cada arquivo `.json` neste diretório é um workflow independente no formato:

```json
{
  "meta": { "id": "...", "title": "...", "description": "...", "compatible_models": [...], "required_params": [...], "optional_params": {...} },
  "graph": { ... grafo de nós no formato de API do ComfyUI ... }
}
```

O `graph` usa placeholders `"${NOME}"` que o Workflow Manager substitui em tempo de execução pelos valores enviados pelo backend (parâmetros do usuário + arquivos do modelo escolhido, vindos de `/models/registry.json`).

A aplicação nunca fica presa a um único workflow: novos arquivos podem ser adicionados aqui sem tocar no núcleo do backend.

## Workflows atuais

- `flux-kontext-txt2img.json` — texto → imagem com FLUX.1 Kontext [dev], sem imagem de referência (uso da Fase 1). **Ainda não validado contra uma instância real do ComfyUI** — os nomes de nós (`UNETLoader`, `DualCLIPLoader`, `FluxGuidance`, `BasicGuider`, `SamplerCustomAdvanced`, etc.) seguem o grafo padrão publicado para modelos Flux, mas podem variar conforme a versão do ComfyUI/custom nodes instalados no pod. Antes do primeiro uso real, confirme os nomes via `GET /object_info` do ComfyUI (ou rode `scripts/check_comfyui_connection.py`).
