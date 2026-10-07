# Registro de modelos (Persona Engine V2)

Fonte de verdade: `config/model_registry_v2.json` (lido por `backend/app/core/engines/models.py`).
A rota `GET /api/v2/models/licenses` devolve a mesma tabela para o site.

Regras:
- **Sem fallback.** Pedir um checkpoint indisponível dá erro claro; nunca troca para outro modelo em silêncio.
- `auto` = o checkpoint marcado `default` (hoje o RealVisXL).
- Licença incerta = `LICENSE_REVIEW_REQUIRED`. Com `commercial=True`, esses modelos são recusados.
- **Antes de qualquer download, procurar o arquivo.** O script do pod (`scripts/v2_engine/rodar_pod.sh`) usa o arquivo do volume se ele já existir. Só baixa (do Hugging Face oficial) o que falta, no disco temporário, e no fim apaga só o que ele mesmo baixou/linkou. Nada é duplicado no volume.
- Força da LoRA: a do registro (1.0). Nenhuma etapa sobe sozinha; a API recusa `lora_strength` por pedido.

## Checkpoints

| id | Modelo | Licença | Uso comercial | Status | sha256 (início) | Origem |
|---|---|---|---|---|---|---|
| `realvisxl` (padrão) | RealVisXL V5.0 fp16 | CreativeML Open RAIL++-M | permitido com as restrições de uso do RAIL++ | AVAILABLE (baixado por sessão) | 6a35a7855770 | huggingface.co/SG161222/RealVisXL_V5.0 |
| `lustify` | LUSTIFY! ZENITH V9 SDXL | termos do autor no Civitai (base OpenRAIL++-M) | **desconhecido** | **MISSING · LICENSE_REVIEW_REQUIRED** | 1a3abf0bf481 | civitai.com (exige token do usuário) |

O Lustify só entra quando o **usuário** fornecer o token do Civitai (digitado por ele). Não uso espelhos não oficiais.

## LoRAs

| id | O que é | Licença | Observação |
|---|---|---|---|
| `lunavox_sdxl_v1` | identidade da Luna no SDXL (gatilho `lunavox`) | própria | treinada no RealVisXL V5; sha 0a58a72e0d78; força fixa 1.0 |
| `luna_qwen_2511_v1` | identidade da Luna no Qwen-Image-Edit 2511 | própria | |
| `bfs_head_v5` | troca de cabeça (BFS) | MIT | vencedora do benchmark de troca de cabeça com o Qwen 2511 |

## Troca de cabeça

| id | Modelo | Licença |
|---|---|---|
| `qwen_image_edit_2511` | Qwen-Image-Edit 2511 (fp8) | Apache-2.0 |

## Controles e análise

| id | Para quê | Licença | Uso comercial |
|---|---|---|---|
| `controlnet_union_promax` | pose (openpose) e profundidade no SDXL | Apache-2.0 | permitido |
| `dwpose` | esqueleto da pessoa | Apache-2.0 | permitido |
| `depth_anything_v2_small` | profundidade | Apache-2.0 (só a Small; Base/Large são CC-BY-NC) | permitido |
| `instantid` | referência facial no refino do rosto | Apache-2.0 (pesos) | permitido |
| `antelopev2` | detecção/comparação de rosto (InsightFace) | **só pesquisa não comercial** | **LICENSE_REVIEW_REQUIRED** |
| `sam2` | segmentação | Apache-2.0 | permitido |
| `florence2_large` | detecção por texto (roupa, acessórios) | MIT | permitido |

**Atenção, uso comercial:** o InsightFace antelopev2 é usado pelo InstantID e pela validação de identidade. Os modelos dele são só para pesquisa não comercial. Para uso comercial, é preciso licença da InsightFace ou trocar o detector/comparador.

## Geração (V1, sem mudança)

| id | Modelo | Licença |
|---|---|---|
| `z_image` | Z-Image + LoRA da Luna | Apache-2.0 |
