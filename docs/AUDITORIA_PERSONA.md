# Auditoria de consistência da persona — 2026-10-05

Diagnóstico do pipeline real e benchmark controlado. **Nenhuma correção foi
implementada.** Convenção: **EVIDÊNCIA** (arquivo, medida ou imagem vista),
**HIPÓTESE** (plausível, não provada), **CONCLUSÃO** (o que as evidências sustentam).
Notas qualitativas estão marcadas como *observado qualitativamente*.

Arquivos do benchmark: `scripts/benchmark_persona.py`, `scripts/analyze_benchmark.py`,
`docs/testes/benchmark_{A,A13,B,C,D}.jpg`, `docs/testes/benchmark_results.json`.

---

## 1. LORA AUDIT — `luna_zimage_v1`

**EVIDÊNCIA** (`scripts/train_zimage_lora.sh`, `scripts/caption_zimage_dataset.py`, memória do projeto):

| Item | Valor |
|---|---|
| Ferramenta | ai-toolkit (ostris), `diffusion_trainer` |
| Base | `Tongyi-MAI/Z-Image-Turbo`, arch `zimage:turbo` + adaptador de treino `ostris/zimage_turbo_training_adapter_v2` |
| Dataset | 45 imagens de `/workspace/lora_qwen_luna/target` ("fotos que o usuário gerou no ChatGPT") |
| Legendas | Florence-2 + `clean_reference_caption`: `lunavox, a woman, <roupa, pose, cenário>` — cabelo, olhos, pele e **corpo removidos** da legenda |
| Gatilho | `lunavox` |
| Rede | LoRA, linear (rank) 32, alpha 32 |
| Passos | 2500 (usado o checkpoint final; 1500/2000 deram semelhança igual, ~0.50) |
| Batch / acumulação | 1 / 1 |
| LR / otimizador | 1e-4 / adamw8bit |
| Ruído / timestep | flowmatch / weighted |
| Precisão | bf16; transformer e leitor de texto quantizados em qfloat8; low_vram |
| Resolução | buckets `[512, 768, 1024]` |
| Caption dropout | 0.05; `shuffle_tokens: false`; latentes em cache |
| Leitor de texto | não treinado |
| EMA | desligado |
| Não especificado no script | scheduler de LR, dropout da rede, seed do treino, flip/augmentation, crop (ficam no padrão do ai-toolkit) |
| VAE | o do próprio modelo (`ae.safetensors` na geração) |
| Amostras | 768×1152, 9 passos, seed 42 |

**Análise**

1. *Suficiente para identidade?* Parcialmente. **EVIDÊNCIA:** ArcFace médio de 0.487 ± 0.047 (A, n=10). A mesma pessoa costuma dar ≥0.35, então ela é reconhecida, mas fica longe do ~0.62 obtido com referência direta (B).
2. *Variedade corporal?* Sim em poses (andando, sentada, balé, casal); o corpo em si é sempre o mesmo tipo (*observado qualitativamente* no dataset).
3. *Excessivamente sintético?* **Sim: 100% sintético**, gerado pelo ChatGPT a partir de um retrato. As referências da aba Referências (`luna_15`, `pose_01`...) vêm de **outra** fonte sintética (Flux Kontext). Ou seja, a persona tem duas origens visuais.
4. *Overfitting?* **EVIDÊNCIA:** 2500 passos ÷ 45 imagens ≈ 55 épocas com batch 1. A palavra-gatilho aparece **escrita em placas** em 4 de 10 imagens do benchmark D ("Lunavox", "LUNI VOX", "Lunada") e já tinha sido vista antes (memória do projeto). **CONCLUSÃO:** o gatilho está sobreajustado, a ponto de vazar como texto.
5. *Identidade ou aparência específica?* **HIPÓTESE:** a LoRA aprendeu "a mulher do dataset ChatGPT" inteira (rosto + corpo + estilo de foto), porque a legenda não descreve corpo nem cabelo. Isso é bom para o corpo ficar estável, mas amarra estilo e identidade.
6. *Generaliza para novas poses/cenas?* Em cena simples, sim (ver §10). Em cena complexa, a semelhança cai de 0.487 para 0.404 (D).
7. *Explica body drift?* **CONCLUSÃO:** com o mesmo pedido, o corpo **não deriva** (CV das proporções 1,7–5,6%). O que muda o corpo é o **pedido** (biquíni/praia exagera o busto, *observado qualitativamente* em `site_zimage_gerar*.jpg`) e os modos de Pack, em que o corpo vem da foto por desenho.

**Riscos no dataset (EVIDÊNCIA nas 35 fotos locais em `personas/luna/lora_qwen_novas/`):**
- 6 fotos de balé (30–35) num estúdio **com espelhos**: o reflexo dela aparece na foto.
- ~6 fotos de casal (19–22, 28, 29).
- **HIPÓTESE:** isso ensina a LoRA a aceitar "mais de uma de mim" (reflexo) e a contaminar a outra pessoa.
- Há duas colagens em `colagens/`; não consegui confirmar se entraram nas 45.

## 2. DATASET AUDIT

- **Local:** 35 imagens (+ `31b` + 2 colagens) das 45 do treino; as outras 10 estão só no volume.
- **Resolução nativa:** 1086×1448 (3:4) na maioria; também 1145×1374, 1024×1536, 1536×1024 e 1312×1199. No treino caem para buckets de no máximo ~1024 px.
- **Conteúdo:**
  - **Rosto:** muito constante.
  - **Corpo:** sempre curvilíneo, busto grande e cintura fina.
  - **Roupas e cenários:** bem variados.
  - **Pessoas extras:** fotos de casal, reflexos de espelho e pessoas ao fundo (ex.: 08 tem gente no parque).
- **Corpo inteiro:** poucas fotos realmente de corpo inteiro em pé (23 e o balé); a maioria é plano médio.

## 3. TEST IMAGE ANALYSIS (`docs/testes/`)

Vistas: 12 de 17 imagens (não abri `bfs_casal`, `bfs_solo`, `ensaio_casal_foto1`, `lora_zimage_passo500`, `zimage_calcadao_cenas`).

| Imagem | Workflow (evidência) | Problema | Classe |
|---|---|---|---|
| `ensaio_sensual_v1.jpg`, coluna 3 | Chroma + LoRA `luna_chroma_v1` (comentário em `scripts/test_fast_pipeline.py`) | Deitada: quadril e pernas atrás do tronco sem ligação possível ("corpo dentro de corpo") | ANATOMY FAILURE / DUPLICATION |
| `ensaio_sensual_v2.jpg`, coluna 3 | Chroma + LoRA | Mesmo defeito: quadril de lingerie atrás da cabeça, tronco impossível | ANATOMY FAILURE |
| `ensaio_sensual_v1.jpg`, coluna 4 | Chroma + LoRA | Espelho: ela e o "reflexo" com roupas e poses diferentes | DUPLICATION / MULTIPLE SUBJECTS |
| `ensaio_casal_v1.jpg`, coluna 2 | Chroma + LoRA (+ InstantID no rosto do Rafa) | Pedido de casal virou **duas Lunas** (uma de robe, outra de lingerie); o homem sumiu | MULTIPLE SUBJECTS / DUPLICATION |
| `site_zimage_gerar.jpg`, `site_zimage_gerar2.jpg` | Z-Image + LoRA (site) | Busto exagerado nas cenas de praia/biquíni em relação ao dataset | BODY DRIFT (qualitativo) |
| `lora_zimage_closes.jpg` | Z-Image + LoRA | Rosto mais redondo na coluna 2; "Lavox" escrito em placa | FACE DRIFT (qualitativo) / OTHER (gatilho vazando) |
| `lora_zimage_calcadao.jpg` | Z-Image sem LoRA (cima) × com LoRA (baixo), cenas de casal | Rosto consistente; tamanho do busto varia; anatomia ok | BODY DRIFT leve |
| `lora_zimage_passo1000.jpg`, `lora_zimage_etapas.jpg` | checkpoints da LoRA | Rosto estável; cor do cabelo puxa para acobreado; pessoas ao fundo (pedidas pela cena) | — |
| `troca_corpo_inteiro.jpg` | Pack "pessoa inteira" (inpaint + DWPose) | Corpo segue a silhueta da pessoa original (magra) por desenho; decote recortado estranho na linha 2 | BODY DRIFT (por desenho) / COMPOSITION |
| `modos_foto_referencia.jpg` | versão antiga do Reinterpretar | Pessoa continuou loira (já corrigido no commit `b94912f`) | CONDITIONING FAILURE (histórica) |
| `historia_rafa_consistencia.jpg` | Z-Image + troca de cabeça | Luna e Rafa consistentes; nenhum defeito grave | — |

**CONCLUSÃO:** os piores defeitos (anatomia impossível, Luna duplicada, espelho) aparecem nas imagens do **caminho Chroma + LoRA** e em **cenas complexas** (deitada, espelho, casal). Não se reproduziram no Z-Image com pedido simples (§10).

## 4. CURRENT WORKFLOW (principal: `workflows/zimage-txt2img-lora.json`)

```
prompt (pt -> en pelo Ollama) ─► CLIPLoader qwen_3_4b_fp4_mixed (lumina2) ─► CLIPTextEncode ──► positivo
                                                                         └─► ConditioningZeroOut ─► negativo (zerado)
UNETLoader z_image_turbo_int8_convrot ─► LoraLoaderModelOnly luna_zimage_v1 (1.0) ─► ModelSamplingAuraFlow (shift 3)
EmptySD3LatentImage (W×H) ─► KSampler (8 passos, CFG 1, res_multistep/simple, denoise 1) ─► VAEDecode (ae) ─► imagem
```

**Sem:** foto de referência, IP-Adapter, InstantID, PuLID, ControlNet.

Os outros caminhos estão em §6, §7 e §15.

## 5. CURRENT IDENTITY MECHANISM

- **Geração (Z-Image):** só a LoRA mais o gatilho. O texto de identidade não entra quando a LoRA está ativa (ver `build_prompt`/`generation_service`).
- **InsightFace:** antelopev2 na CPU, pelo nó `LunaFaces`, com det_size 1024 e det_thresh 0.35. **Só mede**: semelhança ArcFace com o maior rosto da referência, idade, sexo, yaw e 5 pontos do rosto. Não condiciona a geração.
- **InstantID:** o workflow `sdxl-instantid-face.json` existe, mas **os modelos foram apagados**. **EVIDÊNCIA:** no pod, `models/instantid/` e `models/checkpoints/` estão vazias, e a memória do projeto confirma ("Apagados com ok do usuario: ... InstantID/RealVisXL").
  - **CONCLUSÃO:** o `face_restore` do Persona Engine e o retoque da troca antiga pelo Qwen **falham e são pulados em silêncio**.
- **PuLID:** `pulid_flux_v0.9.1` e o nó `ComfyUI-PuLID-Flux-Chroma` existem, mas são para Flux/Chroma, não para Z-Image.
- **Qwen-Image-Edit 2511:** instalado, com a LoRA `luna_qwen_2511_v1`, a BFS head V5 e a Lightning de 8 passos. Usado na troca de cabeça.

## 6. CURRENT BODY MECHANISM

**"Current pipeline locks facial identity only through the LoRA, and has no body-conditioning mechanism."**

- **Gerar / Persona Engine:** o corpo vem só do que a LoRA aprendeu. O texto de corpo foi tirado do prompt do Z-Image porque induzia biquíni.
- **Pack "só rosto e cabelo"** (BFS): o corpo é o da foto, por desenho.
- **Pack "pessoa inteira"** (inpaint): a pose e a silhueta vêm da pessoa da foto (DWPose + máscara SAM2); a LoRA preenche.
- **Reinterpretar:** a profundidade (silhueta) vem da foto.
- **Não existe:** referência de corpo inteiro na geração, IP-Adapter, *body embedding* nem medida de corpo.

**Por que o InstantID não resolveria:** ele condiciona só o rosto (embedding facial + pontos do rosto) e, aqui, só num recorte do rosto. Além disso, os modelos não estão mais no pod.

## 7. CURRENT POSE MECHANISM

- **Texto para imagem:** nenhum; a pose vem do prompt.
- **ControlNet** `Z-Image-Turbo-Fun-Controlnet-Union-2.1-lite-2602-8steps` (patch `ZImageFunControlnet`; só expõe *strength*, sem start/end):
  - inpaint de pessoa: pose DWPose da foto, força 0.75, com máscara;
  - Reinterpretar: profundidade Depth-Anything V2, força 0,45–0,85.
- **DWPose:** `yolox_l` + `dw-ll_ucoco_384`, resolução 1024.

## 8. CURRENT ANATOMY CONTROL

Nenhum. O negativo é zerado (CFG 1), e não há pose, máscara nem validação anatômica no caminho principal.

## 9. CURRENT VALIDATION (`backend/app/core/validation/validator.py`)

- Detecta todos os rostos (LunaFaces), mas, para cada referência, **fica com o rosto mais parecido e ignora os outros**.
- Não conta pessoas e não valida corpo nem anatomia; cabelo e corpo aparecem como "não medido".
- Exige rosto e confere o sexo do rosto escolhido.
- **EVIDÊNCIA no benchmark D:** 4 de 10 imagens tinham 2 rostos; o validador aceitaria todas pelo rosto principal.

---

## Benchmark (10 imagens por configuração; sementes 100–109; 832×1216; 8 passos; mesmo prompt em A, A13, B e C)

**Prompt simples:**
`lunavox, a woman, full body photo, standing, facing the camera, plain light gray studio background, wearing a black tank top and blue jeans, white sneakers, soft even light`

**Medidas automáticas:**
- **Rosto:** ArcFace com a foto principal (`luna_15`), via LunaFaces.
- **Rostos:** quantos o InsightFace achou.
- **Corpos:** quantos o DWPose achou (pessoa com 4 ou mais pontos com confiança > 0.3).
- **Proporções do corpo principal**, divididas pelo tronco (pescoço → meio do quadril): ombros, quadril, coxas, canelas e braços. "CV" é o coeficiente de variação entre as 10 imagens; quanto menor, mais estável.
- **Limitação:** a projeção 2D depende da pose e da câmera. O CV só é comparável entre configurações com a mesma pose (A, A13, B e C: em pé, de frente).

### 10. BENCHMARK A — Z-Image + LoRA 1.0 (sem referência)
- **Rosto:** 0.487 ± 0.047 (mín. 0.421); idade estimada 28.7 ± 2.3.
- **Pessoas:** 1 rosto e 1 corpo em 10/10.
- **Proporções (CV):** ombros 1,7%, quadril 2,3%, coxas 3,2–3,8%, canelas 4,4–5,6%, braços 2,3–3,2%.
- **Visual** (`benchmark_A.jpg`): 10/10 com anatomia correta, corpo visivelmente igual, uma só pessoa. Numa imagem (s109) apareceu uma **tatuagem** no braço, e a Luna não tem tatuagem.
- **Tempo:** 13 s por imagem (a primeira, com carga do modelo, 58 s).

### A13 — o mesmo com a LoRA 1.3 (o teto do RetryManager)
- **Rosto:** 0.481 ± 0.052, sem ganho. **Idade estimada 31.9 ± 2.1, ou seja, +3,2 anos.**
- **Pessoas:** 1/1 em 10/10.
- **Proporções:** CV parecido (ombros 2,5%, canela esquerda 7,5%).
- **Visual:** anatomia ok em 10/10, com maquiagem mais pesada.
- **CONCLUSÃO:** em cena simples, subir a LoRA **não melhora o rosto e envelhece**. A hipótese de "piora a anatomia" **não se confirmou** em pose simples; poses complexas não foram testadas.

### 11. BENCHMARK B — rosto + corpo por referência (Qwen-Image-Edit 2511, 2 referências, sem LoRA)
O único mecanismo instalado que aceita uma referência de corpo é o Qwen (`TextEncodeQwenImageEditPlus`, com até 3 imagens). Não há IP-Adapter nem condicionamento por imagem para o Z-Image.

**Montagem:**
- image1 = `luna_15` (rosto), image2 = `pose_01` (corpo inteiro);
- instrução "mesmo rosto e mesmas proporções; uma só pessoa";
- latente vazio, 8 passos com a Lightning.

**Resultados:**
- **Rosto:** **0.624 ± 0.018** (mín. 0.594), o melhor e o mais estável. Idade **22.3**, mais jovem que a persona.
- **Pessoas:** 1/1 em 10/10.
- **Proporções (CV):** ombros 1,5%, quadril 1,7%, braços 1,1–2,7%. **O mais estável.**
- **Visual:** quase a mesma foto 10 vezes (mesma pose, mesmo jeans skinny, cara de catálogo). **Diversidade baixa.**
- **Tempo:** 92 s por imagem, **7× o Z-Image**.

### 12. BENCHMARK C — A + pose (DWPose de `pose_01` + ControlNet Union, força 0.6)
- **Rosto:** 0.473 ± 0.059 (mín. 0.351).
- **Pessoas:** 1/1 em 10/10.
- **Proporções:** CV de ombros 3,5% e quadril 3,8%, pior que A.
- **Visual:** as 10 saíram **de frente, paradas**, não na passada de três quartos da `pose_01`. O texto ("standing, facing the camera") venceu o controle de pose.
- **CONCLUSÃO:** com força 0.6 e texto conflitante, o ControlNet **não controla a pose** neste modelo. Para medir o efeito real, falta um teste com o texto coerente com a pose.

### 13. BENCHMARK D — C + cena (calçada em frente a um café em São Paulo)
- **Rosto:** **0.404 ± 0.053** (mín. 0.324), a maior queda.
- **Rostos por imagem:** `[1,1,2,1,1,2,1,1,2,2]`. **Corpos (DWPose):** `[4,1,6,1,1,2,3,4,2,3]` — 6 de 10 com mais de um corpo, todos **passantes ao fundo**, nenhum duplicado.
- **Proporções:** CV de ombros 13,8% e quadril 11,6%. Parte disso é a pose variada (uma de costas, s107), não necessariamente mudança de corpo.
- **Visual:** anatomia ok em 10/10. **"Lunavox" escrito em placas em 4/10.**
- **CONCLUSÃO:** a cena realista é o que derruba o rosto e traz pessoas extras. O corpo segue estável onde a pose é parecida.

### 14. QWEN-IMAGE-EDIT EVALUATION
- **Disponível:** modelo fp8mixed, Lightning de 8 passos, LoRA da Luna (`luna_qwen_2511_v1`) e BFS. Aceita várias referências (image1..3) mais instrução de cena; serve tanto para gerar do zero (B) quanto para editar uma imagem existente.
- **A favor (medido):** o rosto mais fiel (0.62 contra 0.49) e o corpo mais estável.
- **Contra (medido ou observado):**
  - 7× mais lento;
  - diversidade baixa, porque copia pose e roupa da referência;
  - rejuvenesce (22 anos);
  - no pod de 24 GB não cabe junto com o Z-Image (troca de modelo com recarga de ~60–160 s).
- **HIPÓTESE a testar (benchmark E):** usar o Qwen como **segunda etapa**. O Z-Image faz cena, pose e roupa variadas (13 s); o Qwen recebe essa imagem mais rosto e corpo de referência e só ajusta a pessoa. Isso combinaria a diversidade de A com a fidelidade de B.

### 15. CONTROLNET EVALUATION
- Modelo `Z-Image-Turbo-Fun-Controlnet-Union-2.1-lite` (pose, profundidade, canny, inpaint). O nó só expõe `strength`; não há start/end.
- **Hoje controla de verdade só no inpaint** (força 0.75, com máscara, pose da própria foto) e na profundidade.
- **No texto para imagem (C):** força 0.6 não impôs a pose contra o texto.
- **Bom uso possível:** um esqueleto tirado de uma referência de corpo inteiro **da própria persona** leva as proporções dela (comprimento dos membros), mas só para aquela pose.

### 16. FLORENCE-2 EVALUATION
- **Disponível:** `Florence-2-large` e `Florence-2-large-PromptGen-v2.0`.
- **`caption_to_phrase_grounding("person")` devolveu 1 caixa em 50/50 imagens**, inclusive nas de D com 2–6 pessoas. **Não serve para contar pessoas.**
- A tarefa `<OD>` (detecção de objetos) não foi testada.
- **O DWPose contou corretamente** (1 nas cenas de estúdio, 2–6 com passantes). **CONCLUSÃO:** para contar sujeitos, use DWPose junto com os rostos do InsightFace, filtrando pessoas pequenas ou ao fundo.

### Tabela comparativa (só medido ou observado)

| Arquitetura | Rosto (ArcFace) | Corpo (CV ombros/quadril) | Anatomia | Pose | Flexibilidade | Custo |
|---|---:|---:|---:|---:|---:|---:|
| Z-Image + LoRA 1.0 (A) | 0.49 ± 0.05 | 1,7% / 2,3% | 10/10 ok | livre (texto) | alta | 13 s |
| Z-Image + LoRA 1.3 (A13) | 0.48 ± 0.05 (+3 anos) | 2,5% / 1,9% | 10/10 ok | livre | alta | 13 s |
| Z-Image + Face Reference | não existe no pod | — | — | — | — | — |
| Qwen 2511 rosto + corpo (B) | **0.62 ± 0.02** | **1,5% / 1,7%** | 10/10 ok | copia a referência | baixa | 92 s |
| Z-Image + LoRA + pose (C) | 0.47 ± 0.06 | 3,5% / 3,8% | 10/10 ok | **não obedeceu** | alta | 15 s |
| C + cena (D) | **0.40 ± 0.05** | 13,8% / 11,6%* | 10/10 ok | variada | alta | 15 s |
| Híbrida (Z-Image → Qwen com referências) | **não testada** (benchmark E) | — | — | — | — | ~105 s |

\* inclui a variação de pose.

## 17. ROOT CAUSE ANALYSIS

| Problema | Gravidade | Causa provável | Evidência | Solução |
|---|---|---|---|---|
| Anatomia impossível / corpo dentro de corpo | CRITICAL | Caminho Chroma + LoRA (hires 1,5× + retoque) e poses complexas (deitada) | `ensaio_sensual_v1/v2`, coluna 3; não ocorreu em 50 imagens Z-Image | Não usar o Chroma para a persona; limitar a complexidade de pose; validar a anatomia com DWPose |
| Luna duplicada / duas pessoas | CRITICAL | Pedidos de casal com uma LoRA que vale para o modelo inteiro (a identidade vaza para a outra pessoa); espelhos no dataset | `ensaio_casal_v1`, coluna 2; balé com espelho no dataset | Modo uma pessoa por padrão; casal só com etapa própria (troca de cabeça por pessoa); dataset v2 sem espelho; contagem de sujeitos |
| Body drift | HIGH | Nenhum condicionamento de corpo; o corpo é o que a LoRA aprendeu, e o pedido o puxa (biquíni) | CV ≤ 6% com o mesmo pedido; busto exagerado nas cenas de praia (qualitativo); referências master de outra fonte | Definir a referência de corpo master; Qwen como 2ª etapa (testar E) ou LoRA v2 com corpo coerente |
| Face drift em cena real | HIGH | Cena complexa dilui a LoRA | 0.49 → 0.40 (A → D) | Etapa de identidade por referência (Qwen); LoRA v2 |
| Gatilho escrito em placas | HIGH | LoRA sobreajustada (~55 épocas, 45 imagens) | 4/10 em D | LoRA v2: menos passos e/ou gatilho raro, regularização |
| Retry sobe a LoRA | MEDIUM | `retry.py` leva `identity_strength` a 1.0, que vira LoRA 1.3 | A13: rosto igual e +3 anos | Não subir a LoRA; mudar de estratégia (semente, cena mais simples, 2ª etapa) |
| Retoque de rosto inexistente | MEDIUM | InstantID e RealVisXL apagados | `models/instantid` vazio | Marcar `supports_face_restore=false` ou reinstalar (decisão) |
| Validador ignora pessoas extras | MEDIUM | Escolhe o rosto mais parecido | D: 2 rostos em 4/10 | Contar todos os rostos e os corpos do DWPose |
| Pod desligou no meio do teste | MEDIUM | `idle_shutdown` desliga com uma única checagem "livre" entre duas tarefas do ComfyUI | `luna-idle-stops.log`: "ocioso há 723 s" durante o benchmark | Tolerância (n checagens seguidas) ou keepalive para tarefas diretas |
| Criar pod no US-MO-2 falha | LOW | API REST da RunPod não aceita mais `US-MO-2` em `dataCenterIds` | resposta 400 no `/api/runpod-wake` | Atualizar a lista de datacenters em `_runpod.ts` |
| Resolução acima do treino | LOW (hipótese) | Scripts usam 864×1536 (1,33 MP); o treino foi até ~1 MP | `test_fast_pipeline.py`, buckets do treino | Gerar até ~1 MP (832×1216) |

## 18. RECOMMENDED ARCHITECTURE

Baseada no que existe no pod (Z-Image Turbo, LoRA, ControlNet Union, DWPose, InsightFace, Qwen 2511):

1. **Etapa de cena: Z-Image Turbo + LoRA a 1.0**, uma pessoa por padrão, até ~1 MP. É rápida, variada e anatomicamente limpa em pose simples (medido).
2. **Etapa de identidade e corpo: Qwen-Image-Edit 2511 com referências master** (rosto + corpo inteiro), editando a imagem da etapa 1, só quando a validação pedir ou para sequências longas. **Depende do benchmark E.**
3. **Validação:** DWPose (quantos corpos, pontos plausíveis, proporções contra a master na mesma classe de pose) + InsightFace (todos os rostos, ArcFace) + semelhança calibrada.
4. **A médio prazo, LoRA v2:** dataset coerente com a referência master, sem espelhos e sem segunda pessoa (ou legendado), com uma parte de corpo inteiro, menos repetição, e conferência das amostras no passo 500. É o que mantém 13 s por imagem com identidade mais forte em centenas de gerações.

**Não fazer:**
- subir a força da LoRA como correção;
- contar com InstantID (apagado) ou com o Florence de frase para contar pessoas;
- usar a última imagem gerada como referência.

## 19. PERSONA LOCK SPECIFICATION

- **HARD LOCK:**
  - rosto (ArcFace ≥ limiar calibrado contra a master);
  - estrutura facial (proporções dos 5 pontos, de frente);
  - cor e comprimento do cabelo;
  - tom de pele;
  - proporções do corpo (ombros/quadril/membros ÷ tronco dentro da tolerância da master, mesma classe de pose);
  - sem tatuagem;
  - uma só pessoa com a identidade da persona por imagem.
- **SOFT LOCK:** expressão, maquiagem, penteado, pose (complexidade LOW por padrão), iluminação, estilo fotográfico.
- **FREE:** roupa, cenário, local, ação, enquadramento, desde que a anatomia seja válida e o número de pessoas seja o pedido.

## 20. IMPLEMENTATION PLAN (não implementado)

1. **Correções pequenas e seguras** (quando aprovar):
   - RetryManager sem subir a LoRA acima de 1.0;
   - `supports_face_restore=false` enquanto não houver InstantID;
   - validador contando todos os rostos;
   - tolerância no `idle_shutdown`;
   - lista de datacenters da RunPod.
2. **Benchmark E** (~20 min de GPU): Z-Image → Qwen com rosto + corpo de referência, 10 imagens no prompt simples e 10 na cena D. Medir rosto, CV e diversidade.
3. **Benchmark C2:** pose com o texto coerente (andando de três quartos) e forças 0.6/0.8/1.0, para saber se o ControlNet controla a pose no texto para imagem.
4. **Master da persona:** escolher **uma** fonte (as fotos do ChatGPT batem com a LoRA) e marcar 1 PRIMARY, 1–2 FACE, 1 FULL_BODY de frente e 1 PROFILE. Registrar as proporções de corpo da master.
5. **Validadores reais:** contagem de sujeitos (DWPose + InsightFace), anatomia por plausibilidade dos pontos do DWPose (membros faltando ou comprimentos impossíveis) e corpo contra a master. A Quality Gate composta da especificação em cima disso.
6. **LoRA v2** (opcional, ~US$ 1, ~2 h): dataset revisto, menos passos, conferência no passo 500.
