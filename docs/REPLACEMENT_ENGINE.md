# Replacement Engine (V2)

Troca a pessoa de uma foto pela Luna e mantém a **fotografia**: enquadramento, pose, perspectiva, roupa, cenário e luz.
O que é da pessoa original sai: rosto, cabelo, tatuagens e marcas.

Código: `backend/app/core/engines/replacement.py` (núcleo, sem ComfyUI), `backend/app/providers/comfyui/sdxl_engine.py` (adapter),
`backend/app/providers/comfyui/engines_v2.py` (fábrica), `config/persona_replacement_v2.json` (prompts, negativos, limites).
API: `POST /api/v2/replace` (ver `docs/PERSONA_ENGINE.md`).

## Fluxo

```
foto original
 1. análise da cena ............ rosto-alvo, corpo, outras pessoas (ComfyReferenceReader)
 2. segmentação ................ pessoa principal x secundárias, cabelo, roupa (Florence), acessórios
 3. pose ....................... DWPose da foto ORIGINAL
 4. profundidade ............... Depth Anything V2 Small da foto original (na tatuagem: da imagem LIMPA)
 5. proteção ................... roupa, fundo e acessórios ficam fora da máscara
 6. identidade ................. LoRA lunavox (força fixa) + master facial + referências
 7. reconstrução RealVisXL ..... rosto + cabelo + pescoço (ControlNet Union: pose + profundidade)
 8. refino do rosto ............ InstantID com a master (só se a identidade ficar abaixo do limite;
                                  MAX_QUALITY sempre). Não é troca crua de rosto.
 9. refino do corpo ............ faixa de transição pescoço/ombros (MAX_QUALITY ou degrau F+)
10. limpeza de tatuagem ........ zona da tinta preenchida antes; profundidade da imagem LIMPA; sem LoRA;
                                  tom casado com a pele conhecida ao redor; mede tattoo_residual_score
11. cabelo/mão/borda ........... transição suave só para DENTRO da máscara
12. integração ................. degrau de tom pele-pele na borda, harmonia rosto/corpo parcial,
                                  só o grão que falta (sem filtro, sem grão pesado)
13. validação V2 ............... por dimensão (PASS/WARN/REJECT, score, limite, motivo)
14. aceitar / tentar de novo (correção específica) / reprovar
```

Garantia de isolamento: depois de cada passe, **tudo o que está fora da máscara volta a ser a foto original**, pixel a pixel.
Os testes conferem isso sujando a imagem fora da máscara no adapter falso.

## Modos

| Modo | O que liga |
|---|---|
| FAST | sem profundidade, 25 passos |
| QUALITY (padrão) | pose + profundidade + referência facial + limpeza de tatuagem + integração, 30 passos, até 2 novas tentativas |
| MAX_QUALITY | + refino do corpo + hi-res do rosto (1536), 40 passos, refino de rosto sempre, até 3 novas tentativas |

Degraus do benchmark (`policies.LADDER`):

| Degrau | Liga |
|---|---|
| A | nada |
| B | + refino + referência facial |
| C | + pose |
| D | + profundidade |
| E | + segmentação |
| F | + corpo + hi-res |
| G | + tatuagem |
| H | + integração |

## Validação (por dimensão)

| Dimensão | Mede | PASS / REJECT |
|---|---|---|
| identity | semelhança ArcFace com a Luna | ≥ 0,70 / < 0,50 |
| original_residual | semelhança com o rosto ORIGINAL, tinta e cabelo que sobraram | rosto < 0,30 (rejeita ≥ 0,40); tinta ≤ 0,03 (rejeita > 0,15); cabelo ≤ 0,08 (rejeita > 0,30) |
| tattoo | `tattoo_residual_score` (fração da tinta original que ficou) | ≤ 0,03 / > 0,15 |
| pose | distância da pose original | ≤ 0,12 / > 0,25 |
| background | fração alterada fora da pessoa | ≤ 0,002 / > 0,02 |
| skin | textura (0,55 a 1,9 da original) e tom rosto × corpo (ΔE ≤ 6, aviso até 10) | |
| body | largura de ombros final/original | ± 8 % |
| duplicate_persona | Luna aparece mais de uma vez | pessoas ao fundo são permitidas |
| anatomy | | UNKNOWN: ainda não há detector de mãos/dedos, conferir no olho |
| composition | deslocamento global | ≤ 0,01 / > 0,05 |

Falhas graves (REJECT direto): rosto não encontrado, rosto original sobrando, fundo alterado, Luna duplicada.

## Nova tentativa (por tipo de falha)

| Falha | Correção |
|---|---|
| identidade | mais referência facial (até 0,6) e mais denoise no rosto; refino forçado. **A LoRA não sobe.** |
| tatuagem | margem maior e mais denoise na zona |
| pose | ControlNet de pose mais forte |
| fundo | máscara encolhida e denoise menor |
| corpo | liga o refino do corpo |
| pele | liga a integração |

Fica sempre a melhor tentativa. O número de tentativas é limitado pelo modo.

## Telemetria

Cada job registra:
- identificação: job_id, persona_id, engine, mode, model, provider, engine_version, workflow_versions, license;
- modelos: checkpoint_hash, lora_hash;
- parâmetros: seed, steps, cfg, resolution, controlnet_strength, reference_strength, denoise, passes;
- desempenho: vram_peak_mb, duration_s, gpu_seconds, estimated_cost_usd;
- resultado: validation_result, failure_reason, retry_count, retries.

No backend, a telemetria vai também para `logs/engines_v2_telemetry.jsonl`.

## Retenção e privacidade

- Processamento **só local**: o ComfyUI roda no próprio pod e nenhuma imagem vai para API externa. A API recusa `processing` diferente de `local`.
- Intermediárias (`repl_*`) e uploads (`v2in_*`) são apagados depois de 24 h. Resultados finais (`repl_final_*`, `repl_faceswap_final_*`) ficam 30 dias. Ver `config/engines_v2.json`.
- Masters são imutáveis (sha256 na Persona Sheet). Nenhum resultado vira master.

## Limitações conhecidas

- **Mão tatuada:** em qualquer denoise a mão vira "massa" ou textura de pelo (testes de transferência 1 a 7 e retoque). Ainda sem solução boa.
- **Anatomia:** sem detector automático de mãos/dedos.
- **Argola/maquiagem:** vêm da referência principal da Luna (ela usa argola grande e maquiagem forte). Melhorar trocando a referência, não o pipeline.
- **Lustify:** indisponível até o usuário fornecer o token do Civitai.
- **InsightFace (antelopev2):** licença não comercial (ver `docs/MODEL_REGISTRY.md`).

## Autoridade de atributos (spec 45)

**Princípio:** a foto original fornece o CONTEXTO, e a Persona fornece a IDENTIDADE. "Preservar a foto" não é preservar os pixels da pessoa original: é obedecer à política de atributos. Nada que aparece na foto é "da Luna" por inferência.

Código:
- `backend/app/core/engines/attributes.py`: políticas e resolução.
- `replacement.py`: máscaras, condicionamento e etapas.
- `validation.py` (`attribute_policy`) e `retry.py`.
- `skin.py`: entrada da reconstrução de pele.

**Precedência:** padrão do Replacement < `replacement_policy` da Persona Sheet < pedido explícito (`preserve_attributes`, `remove_attributes`, `reconstruct_attributes` na API e na tela).

| Atributo | Padrão | O motor aceita |
|---|---|---|
| pose, enquadramento, ângulo, perspectiva, roupa, cenário, luz, objetos | PRESERVE | só PRESERVE (não regenera roupa nem cenário) |
| expressão | PRESERVE | PRESERVE / RECONSTRUCT |
| acessórios (óculos, boné) | PRESERVE (regra da Luna: óculos mantidos) | PRESERVE / REMOVE |
| rosto, pele | RECONSTRUCT | só RECONSTRUCT |
| corpo, cabelo | RECONSTRUCT | RECONSTRUCT / PRESERVE |
| tatuagens, cicatrizes, piercings, pintas, maquiagem, joias | REMOVE | REMOVE / PRESERVE (só quando pedido) |
| outras marcas da pessoa original | REMOVE | só REMOVE |

O mesmo atributo em duas listas, ou uma política que o motor não executa, dá erro 400 **antes** de usar GPU. Nomes livres em `preserve` (ex.: `black_top`) viram "keep the black top" no prompt. Nomes livres em `remove` vão para o negativo.

**Persona Sheet da Luna** (`personas/luna/persona_sheet.json`):
- `identity_attributes`: rosto, corpo, pele (com `skin_reference` = master_body, que orienta e não é copiada), cabelo, idade e traços próprios (gargantilha, pingente, argolas pequenas).
- `identity_exclusions`: listas vazias, ou seja, a Luna não tem tatuagem, cicatriz, piercing nem pinta.
- `replacement_policy`: o padrão dela.
- É uma emenda da versão 1.0 (sem mudança de identidade nem de geração).

**Onde a política age** (não só na UI):

| Camada | O que faz |
|---|---|
| Prompt | camada positiva de exclusão ("clean natural skin, no tattoos...") e o que preservar ("same facial expression", "keep the black top"). Maquiagem PRESERVE tira o "no makeup" |
| Negativo | marcas da pessoa original ("tattoo outline", "ink on skin"...). O que for PRESERVE sai do negativo |
| Máscaras | `source_identity_mask`, `source_face_mask`, `source_hair_mask`, `source_body_mask` e `source_markings_mask` (prioridade de remoção, com buracos fechados e margem). Cabelo PRESERVE fica fora da geração. Cada caixa detectada segue o atributo do **rótulo**: óculos PRESERVE ficam protegidos e são colados de volta; brinco/pulseira REMOVE não são protegidos |
| Inpaint | reconstrução de pele nas marcas: entrada sem a marca (push-pull, sem blocos) → RealVisXL **com a LoRA da Luna**, denoise 0,8, profundidade da pele limpa + pose → refino local leve (0,3) → integração de textura só na borda → resíduo medido. Nada de borrar, pintar cor ou clonar vizinho como resultado |
| Validação | `attribute_policy`, um veredito por atributo. Violação de REMOVE ou PRESERVE, ou rosto original sobrando, é **REJECT mesmo com identidade alta**. Sem medida = UNKNOWN (listado) |
| Retry | a violação vira a falha do atributo: marcas → máscara ampliada + reconstrução de pele; rosto original → reconstrução do rosto; pele → refino de pele; identidade → condicionamento de identidade |
| Telemetria | `attributes`: política, origem de cada atributo, itens livres e a decisão por caixa detectada |

**Limites honestos:**
- O detector de marcas é o de **tinta/marca escura na pele**: ele não separa tatuagem de cicatriz.
  - Com tatuagens PRESERVE, a limpeza não roda (apagaria a tatuagem pedida), e cicatriz/pinta ficam UNKNOWN.
  - Piercing, joia e maquiagem também não têm detector: o rosto é reconstruído e o veredito é UNKNOWN (conferir no olho).
- Tatuagem em **mão** continua sendo o caso mais difícil. A reconstrução nova (LoRA + pose + profundidade da pele limpa, sem blocos) ainda **não foi testada na GPU**.

## Separação semântica (spec 46, 2026-10-08)

A pessoa da foto não é um bloco único de pixels. Cada categoria tem política própria:
IDENTITY, BODY, SKIN, POSE, HANDS, HAIR, CLOTHING, ACCESSORIES e MARKINGS.

**Preservar não é travar pixels (46.1).**

| Categoria | O que o "preservar" faz |
|---|---|
| Mão | Mantém posição, gesto e relação com os objetos; a anatomia e a pele são refeitas (HAND_POSE_LOCK) |
| Óculos | Mantém o objeto; o rosto atrás da lente é o da Persona |

**Acessório por objeto (46.2).** Óculos, brincos, colar, pulseira, relógio, anel e boné viram camadas.
- Cada camada tem `accessories.py`: máscara, caixa, ordem de oclusão e política própria. A política vale por item; sem item, vale a da classe.
- A pessoa é refeita por baixo, e as camadas mantidas voltam por cima, na ordem: óculos/boné na frente do rosto; brinco e colar; pulseira e relógio.

**Óculos (46.3):**
- **Lente escura** (óculos de sol): lente + armação voltam inteiras.
- **Lente clara:** só a armação volta.
  - Armação = traço escuro **ligado à borda** dos óculos. Mancha escura solta dentro da lente (o olho original) não é armação.
  - O tom da lente é reaplicado sobre o rosto novo.

**Mão com pose travada (46.4).** É uma etapa própria, depois do corpo e antes do rosto:
- openpose forte (com as mãos) + estrutura da mão original;
- entrada sem tatuagem quando há tinta na mão;
- LoRA da Persona, denoise 0,45;
- aceita só se o DWPose ainda achar os dedos: pontos da mão final / da original ≥ 0,85.
- Mão PRESERVE (pedido explícito) = pixels da foto.

**Corpo da Persona com a mesma roupa (46.12).** Com roupa PRESERVE, a etapa do corpo refaz só a **pele visível** (braços, pernas, barriga, pescoço) com a anatomia da Persona. A roupa fica pixel a pixel. "Roupa redesenhada parecida" continua disponível por pedido.

**Política estruturada (46.10).** `ReplacementRequest.structured_policy` / campo `policy` na API, por exemplo:

```
{"preserve": {"accessories": ["glasses", "earrings", "bracelet"], "clothing": ["top"], "pose": {"enabled": true}},
 "remove": {"markings": ["tattoos", "scars"]},
 "reconstruct": {"identity": ["face", "body", "skin"]}}
```

**Classificação de cada detalhe (46.9).** `telemetry.attributes.source_details`: cada detalhe com a sua política (as mãos com o modo POSE_LOCK / PIXEL_LOCK) e cada objeto detectado com a sua política e camada.

**Validação (46.11):**
- Novas: `hands` (anatomia por pontos de dedo, proxy) e `accessories` (alteração dentro dos objetos mantidos).
- Com retry próprio: mão (outra semente, menos denoise, mais estrutura) e acessório.

**Padrão da Luna:**

| Política | Atributos |
|---|---|
| Mantidos da foto | roupa, acessórios e joias, pose, cenário, luz |
| Da Luna | rosto, cabelo, corpo, pele, mãos (gesto da foto) |
| Removidos | tatuagens e marcas da pessoa original |

**Limites:**
- A anatomia da mão é medida por proxy (pontos do DWPose), não por um detector de dedos dedicado.
- Óculos de lente clara dependem do traço da armação ser mais escuro que o entorno.
- **Nada da spec 46 foi testado na GPU ainda.**

## Spec "Master Implementation" (2026-10-08)

A geração normal da Luna não foi alterada: nada em `core/generation`, nos workflows de geração, nos prompts, na LoRA nem nas seeds.
O teste `test_generation_engine_never_imports_the_replacement` continua garantindo a separação.

### Quem manda em quê

| Autoridade | Componente |
|---|---|
| Cena | foto original: pixels fora da pessoa voltam sempre da foto |
| Identidade | Persona Sheet + `master_face` (imutável; nada gerado vira master) |
| Atributos | `AttributePolicy` (`attributes.py`): PRESERVE / RECONSTRUCT / REMOVE / OPTIONAL / IGNORE |
| Pose | DWPose → ControlNet de pose (`pose.strength`) |
| Reconstrução | RealVisXL V5 + LoRA `lunavox` (força fixa do registro) |
| Condicionamento de identidade | InstantID no refino do rosto (`identity.strength`); não copia rosto |
| Reconstrução local | inpaint por máscara (corpo, mão, rosto, tatuagem) |
| Qualidade | `QualityGate` (`quality_gate.py`): 12 validadores com nome |

O **Qwen-Image-Edit 2511 + BFS não é mais etapa padrão**: `qwen.enabled=false`.
Ele roda só se for pedido (`qwen_face_lock: true` ou perfil `current` do benchmark), e nesse caso fica em `experimental_stages`.
Não existe fallback silencioso (`fallback_used` e `fallback_model` estão na telemetria).

### Configuração dedicada: `config/persona_replacement_v2.json`

Antes este arquivo se chamava `replacement_engine_v2.json`. A spec pediu um arquivo dedicado e ele foi renomeado, para não ficarem duas configs.

Ele traz `base_model`, `lora`, `identity.strength` (InstantID), `pose.strength`, `depth.enabled/strength`, e os blocos `skin_reconstruction`, `tattoo_removal`, `accessory_preservation` e `source_identity_residual`.
Os quatro últimos têm `hard_fail`. Também traz `adaptive_retry`, `qwen`, `debug`, `quality_profiles`, `benchmark_ranges` e `hard_fail`.

As forças são pontos de partida, não valores medidos:

| Força | Valor inicial | Faixa para benchmark | Observação |
|---|---|---|---|
| pose | 0,8 | — | valor das rodadas reais |
| profundidade | 0,35 | 0,25–0,45 (spec) | antes 0,5, não medido; vale só para corpo e pele, nunca rosto e cabelo |
| InstantID | 0,5 | — | — |

As faixas ficam em `benchmark_ranges`.
O pedido sobrescreve a config: `ReplacementRequest.identity_strength/pose_strength/depth_strength` ou `advanced`.

### ReplacementRequest (spec 29)

- `replacement_version`: `"v2"`. A V1 (`core/persona_replacement`) segue intacta e roda pelo script.
- `pose_required`, `clothing_required`, `accessories_required`, `remove_tattoos`: `None` mantém o que diz a ficha. Quando vêm, viram a política de atributos.
- `quality_profile`: `fast`, `balanced` ou `hyperrealistic`, mapeando para FAST, QUALITY e MAX_QUALITY.
- `identity_strength`, `pose_strength`, `depth_strength`.
- `debug`.
- `qwen_face_lock`: experimental.

Na API, esses campos vão em `POST /api/v2/replace` com `replacement={...json...}`. Campo desconhecido ou `v1` resultam em 400 antes de usar a GPU.

### Análise da foto (spec 5) e máscaras (spec 6)

`scene_analysis.analyze_scene` devolve dados estruturados; nenhum modelo gera imagem nesta etapa:

- número de pessoas;
- cena: lugar, luz medida, câmera;
- pose: orientação do corpo, rotação da cabeça, cada braço, pontos das mãos;
- roupa (top/bottom), cabelo de origem, acessórios com política e camada;
- `undesired_attributes` com a parte do corpo pelo esqueleto, por exemplo `tattoo_right_forearm`.

`semantic_masks` gera as máscaras `person`, `face`, `skin`, `hair`, `hand`, `clothing`, `accessory`, `tattoo`, `background`, `neck`, `arm`, `leg`, `source_identity` e `source_marking`.
A tatuagem tem prioridade sobre a pele: `skin = skin - tattoo`. `check_hierarchy` confere isso em toda execução.

### QualityGate (spec 22/23)

| Validador | Checks |
|---|---|
| IdentityValidator | identity |
| PoseValidator | pose |
| AnatomyValidator | anatomy, body |
| TattooResidualValidator | tattoo |
| SourceIdentityResidualValidator | original_residual (ArcFace do rosto original, cabelo, tinta) + **source_pixel_residual** (fração do rosto/corpo a reconstruir que ficou com o pixel original) |
| SkinConsistencyValidator | skin, seams |
| AccessoryPreservationValidator | accessories + **accessory_objects** (por objeto: presença, posição, forma, cor, escala) |
| ClothingPreservationValidator | clothing |
| HandValidator | hands |
| HairValidator | hair |
| SceneConsistencyValidator | background, composition |
| PersonCountValidator | duplicate_persona, person_count |

Hard fails (REJECT, que nenhum ArcFace alto compensa):

- qualquer tatuagem residual acima do limite de aprovação;
- pele com textura < 35% da foto;
- acessório obrigatório com presença < 0,5;
- rosto ou corpo com > 35% dos pixels originais;
- pessoa nova na imagem;
- rosto original acima de 0,40;
- emenda ou blocos de colagem acima do limite.

Decisão por tentativa: PASS, RETRY ou REJECT. Se sobram só avisos e não há mais retry, o resultado é aprovado com observações (status WARN).

### Retry por falha (spec 24)

Os pesos da LoRA nunca sobem. Cada falha tem a sua estratégia:

| Falha | Estratégia |
|---|---|
| tatuagem | máscara maior + reconstrução de pele |
| mão | refaz só a mão |
| acessório | camada um pouco maior |
| pose | ControlNet de pose mais forte |
| identidade | referência + refino obrigatório |
| resíduo da pessoa original | **máscara da identidade ampliada** (`identity_grow`) + reconstrução localizada |
| pele | hi-res + integração |
| contagem de pessoas | região mais justa |

Os passes são adaptativos: o refino do rosto só roda abaixo do limite de identidade. A limpeza de tatuagem não roda quando o corpo da Persona já refez aquela pele, e isso aparece em `debug.passes_not_run`.

### Debug (spec 36) e registro (spec 35)

`REPLACEMENT_DEBUG=true` (ou `debug: true` no pedido) salva:

- `original` e todas as máscaras com nome;
- `pose_map` (esqueleto do DWPose desenhado);
- `initial_generation`, `face_pass`, `skin_pass`, `tattoo_pass` e `integration_pass`;
- `final`;
- e em `debug`: `scene_analysis`, `validation_report`, `gate` e `run_log`.

O `depth_map` é gerado dentro do ComfyUI e o workflow não o exporta; isso está registrado.

Toda execução grava `telemetry.run_log` com: `replacement_id`, `persona_id`, `source_image`, `master_face`, `master_body`, `base_model`, `lora`, `lora_strength`, `instantid_strength`, `pose_strength`, `depth_strength`, `seed`, `passes`, `masks`, `validators`, `scores`, `retry_reason`, `final_status`, `processing_time`, `cost`, `fallback_used` e `experimental_stages`.

### "Nodes" (spec 28)

A engine conduz o ComfyUI pela API com nós padrão: inpaint SDXL + ControlNet Union, InstantID, DWPose, Florence-2 e Depth Anything.
As etapas da spec são **módulos Python separados**, não nós customizados instalados no ComfyUI:

| Etapa da spec | Módulo |
|---|---|
| Analyze | `scene_analysis.py` |
| Masks | `_scene` + `semantic_masks` |
| Pose | DWPose do leitor |
| Condition | `attributes.py` + `_conditioning` |
| Generate/Inpaint | `ModelAdapter.inpaint` |
| Tattoo Removal | `skin.py` + `markings.py` + passe `tattoo_cleanup` |
| Integration Pass | `integration.py` |
| Validate/Quality Gate | `validation.py` + `quality_gate.py` |
| Retry | `retry.py` |

Empacotar essas etapas como nós do ComfyUI só faz sentido se forem usadas dentro da interface do ComfyUI. Hoje não são.

### Benchmark A/B (spec 30–32)

- `scripts/v2_engine/plano_benchmark_ab.json`: cada foto roda duas vezes. O perfil `current` é o pipeline das rodadas de 07–08/10 (Qwen BFS, profundidade 0,5). O perfil `v2` é o novo.
- `scripts/v2_engine/relatorio_ab.py`: gera a página lado a lado com original, current, v2 e máscaras, as medidas, a regra de não regressão automática e a coluna de revisão visual.
- A V2 só é recomendada se não piorar tatuagem, acessórios, pose, mãos, resíduo da pessoa original ou emendas, **e** se passar na revisão visual.
- **Benchmark ainda não executado** (precisa de GPU e autorização). Há 7 fotos reais no projeto e a spec pede pelo menos 10.
