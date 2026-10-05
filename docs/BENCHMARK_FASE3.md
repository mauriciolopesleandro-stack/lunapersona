# Fase 3 — Benchmark E + C2, veredito e Persona Sheet (2026-10-05)

Nada foi implementado no sistema. Os workflows, a LoRA, o validador, o RetryManager e a UI estão como antes.
Convenção: **EVIDÊNCIA** (medido), **OBSERVAÇÃO** (visto nas folhas, QUALITATIVO), **HIPÓTESE**, **CONCLUSÃO**.

**Arquivos:**
- `scripts/benchmark_persona2.py` (gera e mede) e `scripts/analyze_benchmark2.py`;
- `docs/testes/bench2_*.jpg` (folhas de contato), `bench2_results.json` (parâmetros e medidas de cada imagem), `bench2_analise.txt` (saída completa da análise);
- `docs/persona_sheet_luna_v1.json`.

## AUDIT (resumo; detalhes em `docs/AUDITORIA_PERSONA.md`)

- **Fallback para o Chroma:** acontece no `GenerationService` quando a LoRA do Z-Image não está no pod. **No benchmark não houve fallback.** Cada resultado registra provider, modelo, LoRA, workflow, semente e resolução; os grafos foram montados direto no ComfyUI (Z-Image ou Qwen), sem passar pelo `GenerationService`. Status de todas as 110 imagens: **VALID**.
- **LoRA, validador e retry:** iguais à auditoria anterior.
  - LoRA: 45 imagens sintéticas, ~55 épocas, gatilho vazando em placas.
  - Validador: fica com o rosto mais parecido e ignora os outros.
  - Retry: sobe a LoRA até 1,3, o que não melhora o rosto e envelhece a Luna.
- **InstantID:** modelos ausentes no pod.

## BENCHMARKS

**Configuração comum:**
- GPU **NVIDIA RTX PRO 4000 Blackwell, 24 467 MiB**, US$ 0,57/h (preço real do pod, `/api/runpod-status`);
- 832×1216; 8 passos; CFG 1;
- **FACE MASTER** = `luna_15_v1` (PRIMARY); **BODY MASTER** = `pose_01_v1`.

**Cenários (mesmos textos em todas as etapas):** estúdio de corpo inteiro, café, passarela externa, andando, perfil leve. Cada um com 2 sementes (200 e 201) = 10 imagens BASE.

**Medidas:**
- **Rosto:** ArcFace com a FACE MASTER (LunaFaces/antelopev2), pegando o melhor rosto da imagem.
- **Sujeitos:** **todos** os rostos (InsightFace) e **todos** os corpos (DWPose, pessoa com 4 ou mais pontos com confiança > 0.3). Se houver mais de 1, `MULTIPLE_SUBJECTS = FAIL`.
- **Corpo:** proporções do DWPose divididas pelo tronco.
- **Preservação** (só nas edições): diferença média de pixels (0–255) contra a BASE, na imagem toda e fora da caixa do corpo (fundo), mais a distância normalizada entre os esqueletos da base e da edição.
- **Aderência à pose:** distância entre o esqueleto do resultado e o esqueleto de origem.
- **Anatomia:** QUALITATIVA, a partir das folhas de contato. Não há medidor automático confiável.

### BENCHMARK E — Z-Image → Qwen

| Resultado | Rosto (ArcFace) | Ganho de rosto (mesma imagem) | Corpo: mudança de proporção vs BASE | Anatomia (QUALITATIVO) | Sujeitos >1 | Cena: fundo / geral / pose |
|---|---|---|---|---|---|---|
| E0 BASE (Z-Image + LoRA) | 0.438 ± 0.067 (0.319–0.517) | — | — | 10/10 ok | 3/10 (pedestres ao fundo) | — |
| E1 QWEN FACE (Qwen 2511 + BFS, cabeça da FACE MASTER) | **0.651 ± 0.047** (0.568–0.739) | **+0.213 ± 0.066** (mín. +0.115) | 2,4% ± 3,1% | 10/10 ok | 3/10 (os mesmos pedestres) | 11.9 / 14.2 / **0.021** |
| E2 QWEN FACE+BODY (Qwen 2511, base + rosto + corpo) | 0.551 ± 0.095 (0.412–0.719) | +0.113 ± 0.138 (mín. −0.076) | 9,5% ± 12,1% (máx. 71%) | 10/10 ok | 3/10 | 19.4 / 28.6 / 0.081 |

**OBSERVAÇÃO** (`bench2_E1.jpg`, `bench2_E2.jpg`):
- **E1:** roupa, pose, mãos e cenário ficam iguais; mudam só a cabeça e o cabelo, que ficam mais escuros, mais ondulados e com a gargantilha da master.
- **E2:** em 10 de 10 imagens a roupa virou o **macacão preto e o salto da BODY MASTER** (o jeans, o vestido vermelho, o vestido branco e a jaqueta sumiram).

**Idade estimada:** BASE 27,4; E1 **22,2**; E2 25,9. A FACE MASTER tem 27.

**CONCLUSÕES:**
1. Para o rosto, o E1 é **melhoria de identidade sem regressão de qualidade** (exceto o rejuvenescimento).
2. O E2 é **IDENTITY IMPROVEMENT WITH QUALITY REGRESSION**: a referência de corpo vestida leva a roupa junto.

### BENCHMARK C2 — Z-Image + DWPose + ControlNet Union (força 0.8, texto coerente com a pose)

| Configuração | Rosto | Corpo (CV ombros / quadril) | Anatomia (QUALITATIVO) | Aderência à pose | Sujeitos >1 |
|---|---|---|---|---|---|
| Z-Image atual, pose livre (benchmark A anterior, mesmo prompt de estúdio) | 0.487 ± 0.047 | 1,7% / 2,3% | 10/10 ok | — | 0/10 |
| C2-A pose simples (em pé, de frente) | 0.463 ± 0.066 | **2,4% / 1,6%** | 10/10 ok | fonte = imagem gerada (não comparável) | 0/10 |
| C2-B 5 poses × 2, estúdio | 0.438 ± 0.071 | 25% / 20% (poses diferentes) | 10/10 ok | **0.094 ± 0.053** (n=6) | 0/10 |
| C2-C as mesmas poses + café | 0.449 ± 0.065 | 24% / 19% (poses diferentes) | 9/10 ok; 1 duvidosa (perna da pose sentada, C2C_pose04_401) | 0.106 ± 0.047 (n=6) | **5/10** (atendentes e clientes) |
| CQ = C2-C → Qwen rosto+corpo | 0.617 ± 0.048 | 5,8% / 12,2% | 10/10 ok | — | 1/10 |

**OBSERVAÇÕES:**
- Com o texto coerente, as passadas, o celular na mão, a pose sentada e o perfil seguiram o esqueleto (`bench2_C2B.jpg`). No benchmark C anterior, com força 0.6 e texto em conflito, a pose foi ignorada.
- **No CQ, o Qwen trocou o cenário** do café pelo parque da BODY MASTER em 6 de 10 imagens (diferença de fundo 73.9, contra 11.9 no E1) e trocou a roupa em 10 de 10.

**Corpo contra a master na mesma pose** (C2B/C2C "walk34", esqueleto da `pose_01`): desvio médio das proporções de **5,2%, 8,8%, 8,9% e 12,2%**. Ombros 0,57–0,61 contra 0,57; quadril 0,30–0,39 contra 0,35.

**CONCLUSÃO:** o ControlNet melhora o controle da pose sem custo de rosto nem de anatomia. Não reduz pessoas ao fundo. A combinação "ControlNet + Qwen só rosto" **NÃO FOI TESTADA** como conjunto; só as duas partes isoladas.

### TESTE DE ESTRESSE (pipeline vencedor: Z-Image + LoRA → Qwen + BFS só rosto)

10 cenas variadas (padaria sentada, ponto de ônibus, varanda rindo, Copacabana, close, banco de parque, cozinha, livraria, mercado, ioga), sementes 500–509.

| | Rosto | ≥ 0.55 | Idade | Sujeitos >1 | Pose vs base | Fundo vs base |
|---|---|---|---|---|---|---|
| Z-Image (ST0) | 0.444 ± 0.067 | 1/10 | 27.7 | 2/10 | — | — |
| → Qwen BFS (STQ) | **0.733 ± 0.079** (0.617–0.870) | **10/10** | 23.7 | 2/10 (Copacabana, parque) | 0.023 | 17.1 |

**OBSERVAÇÃO** (`bench2_ST0.jpg`, `bench2_STQ.jpg`):
- anatomia ok em 20 de 20;
- roupa e cenário iguais;
- gargantilha e pingente voltaram em 10 de 10;
- **"TUNAVOX" escrito numa placa** (ST0_1, e continua no STQ_1).

## PERFORMANCE (medido no pod)

| Pipeline | Etapa | Tempo/imagem (mediana) | 1ª imagem | VRAM pico | Util. média | RAM pico |
|---|---|---|---|---|---|---|
| Z-Image + LoRA | geração | 12,8 s | 54 s (carga) | 12 816 MiB | 71% | 64,6 GB |
| Z-Image + pose | geração | 14,7–15,3 s | 28,6 s | 14 922 MiB | 80–87% | 61 GB |
| Qwen BFS (em lote) | edição | 82 s | 81,8 s | 23 726 MiB | 34% | 67 GB |
| Qwen 3 imagens (em lote) | edição | 92 s | 91,6 s | 23 726 MiB | 42% | 67 GB |
| Pedido avulso (alternando) | Z-Image + troca de modelo | 52,5 s | — | 23 716 MiB | 23% | 67 GB |
| Pedido avulso (alternando) | Qwen BFS + troca de modelo | 162 s | — | — | — | — |
| Qualquer pipeline | validação (LunaFaces + DWPose) | 8,3 s | — | — | — | — |

- Z-Image e Qwen **não cabem juntos** em 24 GB (Qwen chega a 23,7 GB). Alternar por imagem custa **~+40 s no Z-Image e ~+80 s no Qwen** (medido no teste de estresse). Os modelos ficam na RAM (125 GB no pod), por isso a recarga leva dezenas de segundos e não minutos.
- **Lote (batch_size > 1):** NÃO TESTADO.
- **Estabilidade:** 0 erros em 110 gerações e 120 medidas. Um desligamento por inatividade no meio do benchmark anterior (ver auditoria); aqui usei keepalive.

## RUNPOD COST (US$ 0,57/h, RTX PRO 4000; inclui validação de 8,3 s; não inclui ligar o pod)

| Pipeline | s/imagem | US$/imagem | img/h | 100 | 500 | 1.000 | 10.000 |
|---|---|---|---|---|---|---|---|
| Z-Image + LoRA | 21,1 | 0,0033 | 171 | 0,33 | 1,67 | 3,34 | 33,4 |
| Z-Image + pose | 23,2 | 0,0037 | 155 | 0,37 | 1,84 | 3,67 | 36,7 |
| **Z-Image → Qwen BFS, em lote** | **103** | **0,0163** | **35** | **1,63** | **8,16** | **16,3** | **163** |
| Z-Image → Qwen BFS, pedido avulso | 223 | 0,0353 | 16 | 3,53 | 17,6 | 35,3 | 353 |
| Z-Image → Qwen 3 imagens, em lote | 113 | 0,0179 | 32 | 1,79 | 8,96 | 17,9 | 179 |

- **Custo fixo por sessão:** o pod leva ~3 min para ficar pronto e se desliga 8 min depois do último uso, ou seja ~11 min ≈ **US$ 0,10 por sessão**.
- **Pod contínuo:** US$ 13,68/dia, independente do volume.
- Para 10.000 imagens em lote seriam ~286 h de GPU. **HIPÓTESE:** uma placa maior (48 GB, com os dois modelos carregados) eliminaria a troca, mas não está disponível hoje no EU-RO-1 até US$ 0,60/h (memória do projeto). NÃO TESTADO.

## PIPELINE COMPARISON

**Notas da tabela:** "Sujeitos" pela regra estrita (qualquer pessoa a mais = FAIL); "Rosto" é a fração de imagens com ArcFace ≥ 0.55.

| Pipeline | Rosto | Corpo | Anatomia | Sujeitos | Pose | Cena | Tempo | Custo | Veredito |
|---|---|---|---|---|---|---|---|---|---|
| 1. Z-Image + LoRA | 0.44 (≥0.55 em 1/20) | estável na mesma pose (CV ≤ 3%) | ok (QUALITATIVO) | FAIL em cenas públicas (2–3/10) | livre | — | 21 s | 0,003 | **GOOD FOR DRAFTS** (identidade abaixo do limiar) |
| 2. Z-Image → Qwen rosto (BFS) | **0.65–0.73 (≥0.55 em 20/20)** | igual à base (2–4%) | ok | igual à base | preservada (0.02) | preservada | 103 s em lote | 0,016 | **RECOMENDADO (default)** |
| 3. Z-Image → Qwen rosto+corpo | 0.55 | **muda a roupa (10/10)** e até 71% de proporção | ok | igual à base | muda (0.08) | ok | 113 s | 0,018 | **NOT RECOMMENDED** |
| 4. Z-Image + ControlNet | 0.44–0.46 | estável na mesma pose | ok (1 duvidosa) | FAIL no café (5/10) | **segue o esqueleto** | — | 23 s | 0,004 | **GOOD FOR DRAFTS / estágio de pose** |
| 5. Z-Image + ControlNet + Qwen rosto (BFS) | NÃO TESTADO como conjunto | — | — | — | — | — | ~105 s (estimado: soma das etapas) | ~0,017 | **INSUFFICIENT EVIDENCE** |
| 5b. Z-Image + ControlNet + Qwen rosto+corpo (testado) | 0.62 | muda roupa | ok | 1/10 | muda | **troca o cenário (6/10)** | 113 s | 0,018 | **NOT RECOMMENDED** |

## FINAL VERDICT

**Classificação de cada pipeline:**

| Pipeline | Classe | Status |
|---|---|---|
| Z-Image + LoRA | **B** — funciona parcialmente | GOOD FOR DRAFTS |
| Z-Image + Qwen Face (BFS) | **D** — recomendado como pipeline principal | PRODUCTION READY para o rosto; corpo e sujeitos ver riscos |
| Z-Image + Qwen Face+Body | **A** — não recomendado | |
| Z-Image + ControlNet | **C** — recomendado como etapa opcional de pose | |
| Z-Image + ControlNet + Qwen | Face (BFS): **INSUFFICIENT EVIDENCE**; Face+Body: **A** | |

**Respostas:**

1. **Qwen melhora a identidade?** Sim: +0,21 (E1) e +0,29 (estresse), na mesma imagem, 20/20 acima de 0,55.
2. **Qwen melhora o corpo?** Não. No modo só rosto o corpo não muda; no modo com corpo ele estraga roupa e proporções.
3. **Qwen prejudica a anatomia?** Não observado (QUALITATIVO, 40 imagens editadas).
4. **Qwen altera o cenário?** Só rosto: pouco (fundo 12–17 de 255). Rosto+corpo: sim, até trocar o cenário inteiro (CQ).
5. **Qwen altera a roupa?** Só rosto: não. Rosto+corpo: sim, 10/10.
6. **ControlNet melhora a pose?** Sim, com força 0.8 e texto coerente (distância 0,09–0,11). Não, com 0.6 e texto em conflito.
7. **ControlNet melhora a anatomia?** Sem diferença mensurável: a anatomia já estava ok sem ele (0 falhas em pose simples).
8. **ControlNet prejudica a identidade?** Não (0.438–0.463 contra 0.438–0.487).
9. **O número de sujeitos melhora?** Nenhum pipeline tira as pessoas do fundo. Em estúdio, 0/30; em cenas públicas, 20–50% FAIL pela regra estrita.
10. **Maior consistência geral:** Z-Image + LoRA → Qwen BFS (rosto), com ControlNet quando houver pose definida.

**Recomendação explícita:**
1. **DEFAULT:** Z-Image + LoRA → Qwen 2511 + BFS (FACE MASTER), em lote.
2. **Máxima qualidade:** o mesmo, com ControlNet (pose definida) e validação de rosto ≥ 0,55. A combinação precisa do teste que falta (pipeline 5).
3. **Geração rápida:** Z-Image + LoRA, só para rascunho (identidade abaixo do limiar).
4. **Milhares de imagens:** o default, em lote (US$ 16 por 1.000). Pedido avulso custa o dobro.
5. **Não usar mais:** Chroma, Qwen com referência de corpo vestida (E2/CQ), retry que sobe a LoRA, InstantID (não existe no pod).
6. **O Z-Image continua como gerador principal?** Sim: cena, corpo e pose, 13 s.
7. **O Qwen como pós-processamento?** Sim, só cabeça e rosto (BFS).
8. **ControlNet no padrão?** Não no padrão; sim quando o pedido tem uma pose definida.
9. **A LoRA atual continua?** Sim, como suporte (corpo, estilo, cena).
10. **LoRA v2?** Sim, mas não é bloqueante: reduzir a repetição (gatilho vazando), juntar com as referências master e incluir fotos de perfil e de costas.

```
RECOMMENDED PRODUCTION ARCHITECTURE
  SCENE/BODY/POSE: Z-Image Turbo int8 + luna_zimage_v1 @1.0 (8 passos, CFG 1, res_multistep/simple, shift 3, 832x1216)
                   [+ DWPose + ControlNet Union @0.8 quando a cena tem pose definida; texto coerente]
  IDENTITY:        Qwen-Image-Edit 2511 + Lightning 8 passos + BFS head V5, cabeca = FACE MASTER (luna_15_v1), imagem inteira
  VALIDATION:      ArcFace >= 0.55 vs FACE MASTER; todos os rostos + corpos DWPose (regra de sujeitos); proporcoes por classe de pose
  EXECUCAO:        em lote (todas as cenas no Z-Image, depois todas no Qwen)
DEFAULT PIPELINE: o acima, sem ControlNet
HIGH QUALITY PIPELINE: o acima, com ControlNet (pendente: testar o conjunto ControlNet + BFS)
FAST PIPELINE: Z-Image + LoRA sem Qwen (rascunho; nao passa no limiar de rosto)
PIPELINE TO REMOVE: Chroma + LoRA (fallback); Qwen com referencia de corpo; InstantID; retry que sobe a LoRA
LORA DECISION: KEEP como suporte + RETRAIN v2 planejado (nao bloqueante)
```

## LORA DECISION

**USE LORA AS SUPPORT + RETRAIN v2 depois.**

**EVIDÊNCIA:**
- **Corpo e anatomia ok:** 0 falhas visuais em 70 imagens Z-Image; CV ≤ 3% na mesma pose; 5–12% da master na mesma pose.
- **Rosto fraco sozinho:** 0,44, abaixo do limiar.
- **Gatilho vazando** em placas.
- **Fonte sintética diferente** das referências master.

O BFS resolve o rosto sem depender da LoRA, então a v2 não bloqueia a implementação.

## PERSONA CALIBRATION

**Base:**
- as 4 referências master (vistas por mim);
- medidas do DWPose e do InsightFace nas masters;
- o desvio na mesma pose (C2 walk34);
- o limiar de rosto separando a BASE do resultado pós-BFS (20/20 contra 1/20);
- as limitações observadas.

Nenhuma imagem gerada virou referência.

## PERSONA SHEET JSON

`docs/persona_sheet_luna_v1.json` (v1.0, status `CALIBRATED_NOT_DEPLOYED`).

## PERSONA SHEET HUMAN READABLE

**Identidade.** Mulher de fim dos 20 anos (MEDIUM), pele bronzeada/oliva dourada (HIGH).

**Rosto** (HIGH, salvo indicação):
- oval levemente alongado, maçãs altas;
- olhos castanho-escuros amendoados, levemente encobertos;
- sobrancelhas grossas, escuras e quase retas;
- nariz reto de ponta arredondada (MEDIUM);
- lábios cheios com arco do cupido marcado;
- mandíbula suave e queixo arredondado (MEDIUM);
- assimetrias: UNKNOWN.

**Cabelo.**
- **Identidade:** castanho muito escuro, comprido abaixo do peito, ondulado, com reflexos acobreados (HIGH).
- **Variação permitida:** solto, preso, coque, molhado, repartido.

**Corpo.**
- Curvilíneo/ampulheta: busto cheio, cintura fina, quadril arredondado, braços tonificados, pernas longas (MEDIUM).
- Altura UNKNOWN.
- Proporções medidas por classe de pose estão no JSON (não há referência métrica).

**Traços marcantes.** Gargantilha preta fina e pingente oval preto (HIGH, SOFT LOCK); argolas pequenas (VARIABLE); **sem tatuagem (HARD)**.

**Níveis de trava:**
- **HARD:** identidade facial; cor e comprimento do cabelo; tom de pele; sem tatuagem; corpo curvilíneo; uma pessoa com a identidade.
- **SOFT:** gargantilha e pingente; textura do cabelo; expressão padrão; idade aparente.
- **VARIABLE:** penteado, maquiagem, luz, enquadramento, pose, brincos.
- **FREE:** roupa, cenário, local, ação, clima.

**Referências master:**
- `luna_15_v1` = PRIMARY / FACE MASTER;
- `luna_20_v1` = FACE;
- `pose_01_v1` = FULL_BODY, usada **só para medir proporções**, nunca como imagem no Qwen;
- `pose_04_v1` = FULL_BODY sentada;
- perfil e costas: **MISSING**.

**Geração e validação:** ver as seções acima e o JSON.

## NEGATIVE STRATEGY

**EVIDÊNCIA:** Z-Image e Qwen rodam com **CFG 1 e negativo zerado** (`ConditioningZeroOut`). Prompt negativo **não tem efeito** nesses providers.

As três camadas (GLOBAL, PERSONA, SCENE) estão definidas no JSON. Enquanto o provider não suportar negativo, elas valem **como regras de validação**. O ModelAdapter deve declarar `supports_negative_prompt=false` e não fingir que aplica.

A alternativa em texto positivo ("alone", "empty street") está **NÃO TESTADA**.

## VALIDATION PROFILE

- **Rosto:** ArcFace ≥ **0.55** contra a FACE MASTER. Calibrado: separou 20/20 pós-BFS de 19/20 sem BFS.
- **Sujeitos:** `expected_subject_count = 1`; todos os rostos e todos os corpos do DWPose; mais de 1 = FAIL. Uma tolerância para pedestres pequenos ao fundo ainda é **decisão sua**: pela regra estrita, cenas públicas falham em 20–50%.
- **Corpo:** proporções ≤ 15% de desvio contra a master da mesma classe de pose (LOW confidence: 4 amostras comparáveis).
- **Anatomia:** sem medidor automático. 0 falhas visuais em 110 imagens; validador por plausibilidade do DWPose ainda a construir.
- **Idade:** 23–31, porque o BFS tende a 22–24.

## PERSONA COVERAGE

- **SUPPORTED:** frontal, 3/4, close (0,87), meio corpo, corpo inteiro, em pé, sentada, andando.
- **PARTIALLY_SUPPORTED:** perfil (o pedido saiu de 3/4 ou de costas) e movimento (1 amostra de ioga ok; deitada NÃO TESTADA no Z-Image, falhou no Chroma).
- **NOT_SUPPORTED:** costas (rosto não validável), várias pessoas.

## STRESS TEST

10/10 acima do limiar de rosto depois do BFS (0.617–0.870); anatomia ok em 10/10 (QUALITATIVO); roupa, pose e cenário preservados; 2/10 FAIL de sujeitos (pedestres no calçadão, pessoas no parque). **CONCLUSÃO:** a ficha representa a persona no rosto. Corpo e sujeitos dependem dos riscos abaixo.

## KNOWN LIMITATIONS

1. **Corpo sem trava própria** além da LoRA. Nenhum mecanismo instalado transfere só o corpo: o Qwen com referência de corpo leva roupa e cenário.
2. **Rejuvenescimento** do BFS (idade estimada 22–24 contra 27).
3. **Gatilho "lunavox" escrito em placas.**
4. **Pessoas ao fundo** em cenas públicas.
5. **Sem referência de perfil nem de costas.**
6. **Custo:** sem placa de 48 GB, cada pedido avulso paga a troca de modelo (223 s).
7. **Anatomia** só avaliada visualmente.

## RECOMMENDED IMPLEMENTATION PLAN (não executado)

1. **Adapter "comfyui-zimage-bfs":** etapa 1 Z-Image + LoRA (+ ControlNet 0.8 opcional, com texto coerente); etapa 2 BFS com a FACE MASTER; fila em lote; capabilities verdadeiras (`supports_negative_prompt=false`, `supports_face_restore=false` até ter o BFS como etapa).
2. **Validador:** contar todos os rostos e corpos; ArcFace ≥ 0,55; proporções por classe de pose; plausibilidade do DWPose como anatomia.
3. **RetryManager:** semente nova e cena mais simples; sem subir a LoRA.
4. **Remover o fallback do Chroma** (ou marcar o resultado como INVALID).
5. **Teste que falta:** ControlNet + BFS (pipeline 5), 10 imagens.
6. **Persona Sheet** como fonte de verdade no `persona.json` (com versão), referências de perfil e de costas criadas manualmente, LoRA v2.

```
IF I HAD TO DEPLOY THIS TODAY:
DEFAULT: Z-Image Turbo + luna_zimage_v1 @1.0  ->  Qwen-Image-Edit 2511 + Lightning + BFS head V5 (FACE MASTER), em lote
WHY: unico pipeline com 20/20 imagens acima do limiar de rosto (0.65-0.73) sem mudar roupa, pose, cena nem anatomia
IDENTITY: troca de cabeca BFS com a FACE MASTER luna_15_v1
BODY: LoRA (estavel na mesma pose, CV <= 3%; 5-12% da master na mesma pose) - sem trava propria
POSE: livre pelo texto; DWPose + ControlNet Union 0.8 quando definida
VALIDATION: ArcFace >= 0.55 vs FACE MASTER + contagem de todos os rostos/corpos (DWPose) + proporcoes por classe de pose
TIME PER IMAGE: 103 s em lote (12.8 + 82 + 8.3); 223 s pedido avulso
COST PER IMAGE: US$ 0,016 em lote; US$ 0,035 avulso (RTX PRO 4000, US$ 0,57/h)
1,000 IMAGE COST: US$ 16,3 em lote (+ ~US$ 0,10 por sessao de pod)
CONSISTENCY: rosto 0.651 +- 0.047 (E1) / 0.733 +- 0.079 (estresse); corpo CV <= 3% na mesma pose; anatomia 0 falhas visuais
LORA: SUPPORT (+ RETRAIN v2 nao bloqueante)
PRODUCTION READY: NO - pronto para o rosto; falta definir a regra de sujeitos ao fundo e uma trava de corpo alem da LoRA
BIGGEST REMAINING RISK: corpo depende so da LoRA, sem medidor que garanta as proporcoes em todas as poses; pessoas ao fundo
```

NÃO USE: Qwen com referência de corpo inteiro (troca roupa 10/10 e cenário 6/10), Chroma + LoRA (anatomia impossível e Luna duplicada nas imagens antigas), retry que sobe a LoRA (+3 anos, 0 ganho de rosto).
