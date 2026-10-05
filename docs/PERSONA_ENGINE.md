# Persona Engine V1

Produção do pipeline vencedor do benchmark (`docs/BENCHMARK_FASE3.md`).
Auditoria anterior: `docs/AUDITORIA_PERSONA.md`.

```
Persona Sheet (personas/<id>/persona_sheet.json)  ── fonte de verdade, masters com sha256
      │
PromptBuilder + NegativePromptBuilder (global + persona + cena)
      │
[modo POSE_CONTROLLED: DWPose + ControlNet Union 0.8]      PoseControlAdapter
      │
Z-Image Turbo int8 + LoRA luna_zimage_v1 @1.0             SceneAdapter   (ZImageAdapter)
      │  imagem base
Qwen-Image-Edit 2511 + Lightning + BFS head V5            FaceIdentityAdapter (QwenFaceAdapter)
      │  cabeça = master_face (imagem inteira)
Validation Engine (InsightFace + DWPose como MEDIDORES)
      │  face_identity · subject_count · anatomy · pose · body_consistency · age · trigger_leak
      ├── PASS / PASS_WITH_UNKNOWN → ACEITA
      └── FAIL → RetryPolicy (por tipo de falha; nunca mexe na LoRA) → ... → FAILED
```

## Onde está cada coisa

| Peça | Arquivo |
|---|---|
| Persona Sheet (carregar, validar, conferir hash das masters; sem escrita) | `backend/app/core/persona/sheet.py` |
| Proteção das masters (remover, trocar, alterar → 409) | `backend/app/core/persona/references.py`, `routes/personas.py` |
| Pedido (FREE / POSE_CONTROLLED) | `backend/app/core/generation/request.py` |
| PromptBuilder (gatilho só interno) e negativo em camadas | `core/generation/prompt_builder.py`, `negative.py` |
| Orquestrador (avulso e em lote, auditoria, telemetria) | `core/generation/orchestrator.py` |
| RetryPolicy | `core/generation/retry_policy.py` |
| Validadores e motor | `core/validation/checks.py`, `engine.py`, `geometry.py`, `analysis.py` |
| Custo e tempo | `core/telemetry.py`; preço real da GPU em `app/infrastructure/runpod.py` |
| Contratos das etapas | `app/providers/base.py` |
| Etapas no ComfyUI | `app/providers/comfyui/{scene,face,pose,session}.py` |
| Medidores (LunaFaces + DWPose; OCR opcional) | `app/validation_backends/comfyui.py` |
| Configuração do sistema | `config/persona_engine.json` |
| Rotas | `app/routes/persona_engine.py` (`/api/engine/...`) |
| Telas | aba Personas → "Gerar com validação" e "Histórico" |

Regra testada (`test_core_is_provider_agnostic`): nada em `app/core` importa ComfyUI, clientes, serviços, RunPod ou nomes de modelo.

## Rotas novas ou mudadas

- `POST /api/engine/generation` — `{persona_id, scene_prompt, mode: FREE|POSE_CONTROLLED, pose_reference?, max_attempts (1–6), validation_threshold?, style_overrides?}`
- `POST /api/engine/generation/batch` — `{persona_id, scenes: [...]}`: todas as cenas no Z-Image, depois todos os Face Locks (BATCH_MODE)
- `GET /api/engine/personas/{id}/sheet` — resumo da ficha (versão, masters por id e hash, travas, cobertura, limitações), sem caminho de arquivo
- `GET /api/engine/generation/{id}` — job com cada tentativa: sementes, prompt, versões, validação, estágios, métricas e custo

## Validação

| Validador | Bloqueia? | Sem medida | Fonte do limiar |
|---|---|---|---|
| face_identity (ArcFace ≥ 0,55 contra a master_face) | sim | rosto ausente = FAIL | ficha |
| subject_count (outra instância da persona = 2º rosto ≥ 0,40) | sim | corpo grande sem rosto: PASS com confiança LOW e aviso | ficha |
| anatomy | sim | **UNKNOWN** (não há detector validado) → PASS_WITH_UNKNOWN | — |
| pose (só com pose pedida; distância do esqueleto ≤ 0,25) | sim | INFORMATIONAL no modo livre | ficha |
| body_consistency (só se a pose bater com a da master_body) | sim | NOT_COMPARABLE | ficha |
| age (alvo 27; drift conhecido do BFS) | não | — | ficha |
| trigger_leak (OCR do Florence) | sim, se ligado | UNKNOWN com OCR desligado | — |

## Retry

| Falha | Estratégia |
|---|---|
| face_identity_low | refaz só o Face Lock (1x) mantendo a cena; depois regenera a cena |
| face_not_found | regenera a cena com "her face clearly visible" |
| persona_duplicated | regenera a cena com "only one woman in the photo, no mirrors" |
| pose_mismatch, anatomy, body_mismatch, trigger_leak, erro do modelo | regenera a cena com nova semente |

Sementes determinísticas (base + 7919 × tentativa). Nunca muda a força da LoRA.

## Chroma

`config/persona_engine.json` → `"chroma_fallback": false`.
- **No Persona Engine não existe fallback.**
- **Na aba Gerar** (`GenerationService`), uma persona com LoRA do Z-Image fora do pod agora dá erro em vez de cair no Chroma. Ligar o fallback é decisão explícita nesse arquivo.

## Limitações conhecidas (V1)

1. **O corpo não tem trava explícita**: vem só da LoRA. A master_body serve só para validar, e só na mesma pose dela.
2. **A idade aparente pode sofrer drift**: o BFS rejuvenesce (idade estimada 20–25 contra 27). É registrado pelo validador `age`, mas não bloqueia.
3. **Figurantes ao fundo são permitidos**. Uma duplicata da persona **sem rosto visível** não é detectável com os medidores atuais.
4. **O "lunavox" pode exigir tratamento adicional**:
   - **ativo:** o gatilho é removido de qualquer texto do usuário;
   - **desligado e não testado:** a frase "placas sem texto" (`apply_trigger_leak_directive`) e o OCR (`trigger_leak_ocr_check`).
5. **Custo e tempo dependem da VRAM**: 24 GB não cabem Z-Image e Qwen juntos. Em lote ~103 s por imagem; pedido avulso ~223 s.
6. **A anatomia não tem benchmark automático**: o validador responde UNKNOWN.
7. **As masters precisam ser os mesmos arquivos do volume do pod.** O hash da ficha foi tirado das cópias locais; se o volume tiver outro arquivo, o job para com MasterIntegrityError. **Conferir na primeira geração.**

## Testes

```
cd backend
.venv\Scripts\python -m pytest
```

Sem GPU: etapas e medidores falsos em `tests/fakes.py`; masters de teste com hash recalculado (`tests/conftest.py::engine_dir`).

## Próximos passos

- **V1 (implementado):** tudo acima.
- **V1.1:**
  - conferir o hash das masters no volume;
  - medir a frase anti-gatilho e o OCR;
  - detector de anatomia (plausibilidade do DWPose ou modelo dedicado);
  - teste ControlNet + BFS (10 imagens);
  - tirar a chave da RunPod da URL do `idle_shutdown`;
  - tolerância no auto-desligar para tarefas diretas no ComfyUI.
- **V2:** LoRA v2 (corpo, proporções, idade, perfil e costas); referência de corpo que não transfira roupa; trava explícita de corpo.
