# Persona Replacement V1: primeiros testes (2026-10-06)

- **Branch:** `feature/persona-replacement-v1`, com módulo próprio em `core/persona_replacement/`. A geração não foi tocada.
- **Caso de regressão:** a foto da varanda de Copacabana (pessoa original loira, de óculos escuros, corset e jeans).
- **Custo:** teste 1 ~US$ 0,069; teste 2 ~US$ 0,08, incluindo ler o log do teste 1. Ambos autorizados.

| Etapa (teste 2) | Identidade | Idade | Fundo alterado | Roupa alterada | Decisão |
|---|---|---|---|---|---|
| original | 0,21 | 40 | — | — | — |
| cabelo (recolor) | 0,21 | 40 | 0,006% | 0% | aceita |
| rosto 1 (InstantID, denoise 0,60) | 0,47 | 41 | 0,006% | 0% | aceita |
| rosto 2 (refino) | 0,46 | 41 | 0,006% | 0% | aceita |
| rosto 3 (integração) | 0,46 | 41 | 0,006% | 0% | aceita |
| corpo 1 (pele) | 0,45 | 40 | 0,012% | 2,1% | aceita |
| corpo 2 (pele) | 0,44 | 40 | 0,015% | 2,4% | aceita (final) |
| integração luz/grão | 0,43 | 55 | 0% | 2,4% | rollback: identidade −0,013 |

Validação final: **FAIL `identity_low`**, com identidade 0,44.

| Métrica | Valor | Leitura |
|---|---|---|
| Fundo | 0,015% | ok |
| Roupa | 2,4% | ok |
| Pose | 0,008 | ok |
| Luz | 0,95 | ok |
| Textura | 0,50 | razoável |
| Borda | 0,58 | — |
| Halo | 2,5 níveis | — |

O teste 1 (rosto 1 com denoise 0,45) deu identidade 0,43. A textura ficou em 0,07, porque o grão estava sendo medido no fundo, onde mar e prédios são detalhe de cena e não grão.

## Leitura visual
- **Integração fotográfica: excelente.** É a mesma foto: corset, jeans, argolas, óculos, pulseiras, mar e calçadão idênticos, e a pele com a mesma luz. Não parece montagem.
- **Cabelo:** o loiro virou castanho-escuro sem halo nem borrão, mas sobrou uma linha clara fina na raiz (testa/cabelo).
- **Identidade: não virou a Luna.** O rosto mudou pouco: boca e queixo, sem os olhos. Os óculos (protegidos, por regra) escondem os olhos, que são o que mais pesa no ArcFace e na impressão de "quem é".
- **Tatuagens:** continuam visíveis nos braços. As passadas de corpo (0,45/0,20) são fracas demais para apagar.
- **Pele:** um pouco mais bronzeada que a original.

## Conclusão
O método resolve o problema que motivou esta tarefa: a foto continua sendo a mesma foto e não há rosto "colado". Mas ainda não transforma a identidade. Ficou no extremo oposto da rodada 2 do modo replicar, que tinha identidade 0,83 e integração ruim.

## Próximo teste proposto (1 imagem, ~US$ 0,08, só com autorização)
1. **Rosto 1 mais forte:** denoise 0,75, InstantID com peso 1,0 e máscara do rosto incluindo maxilar e bochechas. A integração (rosto 3, luz e grão) segura o aspecto de "colado".
2. **Tatuagem:** passada própria só onde há tatuagem (pele escura dentro da pele), com força maior, sem mexer na roupa.
3. **Raiz do cabelo:** borda suave na recoloração.
4. **Separar o efeito dos óculos:** testar também uma foto sem óculos, para saber quanto da identidade baixa é dos olhos cobertos.

---

# Teste 3: varanda (com óculos) + quarto (sem óculos), ~US$ 0,11

Mudanças: rosto 1 com maxilar e bochechas (InstantID 1,0, denoise 0,75); tatuagem com máscara e passada próprias; raiz do cabelo com borda suave; pulseiras, brincos e relógio protegidos.

| Foto | Identidade | Idade | Fundo | Roupa | Luz | Textura | Borda | Status |
|---|---|---|---|---|---|---|---|---|
| **Quarto (sem óculos)** | **0,825** (0,21 → 0,82 na passada 1) | 24 | **0,0%** | 0,4% | 0,97 | 0,99 | 0,88 | **PASS** |
| Varanda (com óculos) | 0,493 | 38 | 0,07% | 2,0% | 0,95 | 0,18 | 0,67 | FAIL `identity_low` |

## Leitura visual
- **Quarto: virou a Luna e continua sendo a mesma foto.** A pose (mãos puxando o top), o fundo, a rede e a luz ficaram iguais, e a identidade foi alta. Não parece colagem.
- **Varanda:** com os olhos cobertos pelos óculos, a identidade não passa de ~0,5. O resto (fundo, roupa, acessórios, cabelo escuro) está ótimo.

## Ainda falha
1. **Tatuagens continuam nos braços, nas duas fotos.** A detecção por "buracos na pele" só pega traço fino. Tatuagens grandes (rosa no ombro, antebraço) ficam fora: a máscara cobriu só 0,4–0,6% da imagem.
2. **Cabelo do quarto:** as pontas claras (luzes) ficaram. A recoloração só dispara pela média de brilho do cabelo, que era castanha.
3. **Maquiagem:** o rosto do quarto ficou mais "produzido" que o original (blush e boca marcados). O negativo do replacement não tem os termos de maquiagem do estilo da V2.
4. **Óculos escuros:** é um limite físico. Sem os olhos, nem a pessoa nem o ArcFace reconhecem a identidade. Sugestão: limiar próprio (por exemplo, 0,50) para fotos com olhos cobertos, ou a opção de tirar os óculos.

## Próximo passo proposto (só com autorização)
- **Tatuagem:** detectar pela segmentação do Florence ("tattoo"), em vez de buracos na pele, e apagar com mais força só ali.
- **Cabelo:** recolorir quando uma parte relevante do cabelo for clara (percentil 75 do brilho), e não pela média.
- **Maquiagem:** negativo com "heavy makeup, blush, glossy lipstick, contour" no replacement.
- **Teste:** só a foto do quarto (1 imagem, ~US$ 0,06), como regressão.

---

# Teste 4: spec "IMAGE IDENTITY REPLACEMENT" (2026-10-06, ~US$ 0,13, com ~4 min de pod travado no boot)

| Foto | Luna | Semelhança com a ORIGINAL | Idade | Fundo | Roupa | Status |
|---|---|---|---|---|---|---|
| Quarto | **0,845** | **0,025** (sem mistura) | 26 | 0% | 0,3% | PASS (pelas métricas) |
| Varanda (óculos) | 0,484 | 0,457 (misturado) | 56 | 0% | 2,0% | FAIL `identity_low`, `identity_mixing` |

## Leitura visual (honesta): ainda não está boa
- **Quarto:** pelas métricas virou a Luna sem mistura, mas **o rosto continua maquiado e "produzido"** (contorno, bochechas laranja-rosadas, boca brilhante, cílios marcados). Há uma **mancha clara na testa/raiz do cabelo**, um **contorno fantasma no antebraço direito** (da passada de tatuagem) e a tatuagem do ombro esquerdo ficou.
- **Varanda:** a integração de grão deixou **a pele do rosto e do colo manchada/suja**, e a idade estimada foi a 56. Os óculos impedem a identidade. Ficaram fios claros na raiz.

## Por que as métricas aprovaram o que o olho reprova
Identidade, mistura, fundo, roupa, luz e textura são medidas **numéricas**. Nenhuma enxerga maquiagem pesada, mancha, contorno fantasma ou pele suja. Elas são necessárias, mas não bastam.

## Diagnóstico de fundo
- O **InstantID** (identidade forte) tende a gerar rostos contrastados e "maquiados"; a LoRA reforça.
- As correções por pixel (recoloração, grão) resolvem um problema e criam outro (mancha, pele suja).
- O método atual chegou ao limite: **ou integra e não troca, ou troca e fica produzido.**

---

# Modo transferência: spec "PERSONA REPLACEMENT — INTEGRAÇÃO FOTOGRÁFICA" (2026-10-06, só código, sem GPU)

A Luna **inteira** é gerada na pose da pessoa da foto, sem face swap e sem filtro depois.

| Etapa | O que faz | Config (`config/persona_transfer.json`) |
|---|---|---|
| 1. Leitura + pose | pessoa, rosto e pose (DWPose) da foto | — |
| 2. Máscara | pessoa − roupa − acessórios (óculos, pulseira...) + folga de cabelo em volta da cabeça | `hair_room` 0,35 |
| 3. Transferência | RealVisXL + LoRA lunavox @1.0 + ControlNet Union promax (openpose) | denoise 0,9, 30 passos, CFG 5, pose 0,8 até 80% |
| 4. Refino de rosto | **só se** a identidade ficar abaixo de 0,65; sem InstantID | denoise 0,35 |
| 5. Integração | região + costura com a roupa, sem LoRA, sem mexer no fundo | denoise 0,18 |
| 6. Composição | fora da máscara, os pixels da foto, garantido no código | — |

- Checkpoints: `original`, `persona_transfer`, `face_refinement`, `integration`. Com rollback; se a transferência for recusada, o processo para (`transfer_rejected`), porque refinar só o rosto seria face swap.
- Validação: identidade, mistura com a original (> 0,40 reprova), pose, fundo, roupa (fora da costura), luz, textura, borda e `integration_score`.
- O critério final continua sendo **o olho**: "parece uma foto tirada da Luna?".
- Código: `backend/app/core/persona_replacement/transfer.py`, `ComfyTransferTransformer` em `providers/comfyui/replacement.py`, `workflows/realvis-persona-transfer.json`, `scripts/transfer_teste1.py`. 310 testes.

## Transfer teste 1 (quarto, 1 geração, US$ 0,036, 325 s de pod)

Resultado: **FAIL**. A transferência foi recusada pelo rollback ("apareceu outra pessoa/rosto") e o final ficou a foto original.

O que deu certo (olhando a imagem `repl_persona_transfer`):
- Rosto natural, sem maquiagem pesada e sem cara de colagem. Pele com a luz da foto.
- Pose, enquadramento e roupa mantidos. Cabelo escuro e ondulado.
- Geração de 22 s.

O que deu errado:
1. **Folga de cabelo grande demais (23% da imagem):** a elipse cobriu a parede inteira acima da cabeça, e o modelo inventou um quadro com o rosto de uma mulher e um pôster com texto. O cenário foi regenerado (proibido pela spec), e o rosto do quadro disparou o rollback.
2. **Proteção falsa (18% da imagem):** o grounding de acessórios pegou uma caixa enorme (top e mãos). As mãos ficaram as originais, com as tatuagens.
3. **Identidade 0,42:** só a LoRA, com denoise 0,9, não basta. O refino de rosto não rodou porque a etapa foi recusada antes.

## Transfer v1.1: spec "IDENTITY TRANSFER TASK" (só código, sem GPU)
- **Fundo TRAVADO:**
  - a região gerada nunca sai do contorno da pessoa (`hair_room` 0);
  - nenhum pixel fora da máscara vem do modelo (sem a margem de 2 px);
  - o rollback reprova qualquer mudança acima de 0,2% fora da pessoa.
- **Acessórios:** caixas do grounding maiores que o rosto são descartadas como falso positivo. No teste 1, uma delas cobria o top e as mãos.
- **Tatuagens:** saem da ENTRADA da passada 1. A tinta é trocada pela média da pele limpa em volta, e a geração cria a textura da pele. Não é filtro no resultado.
- **Identidade:** vem das referências da Luna. A LoRA atua na passada 1; o refino de rosto, que só roda se a identidade ficar abaixo de 0,70, usa a LoRA com InstantID **moderado (0,5)** e denoise 0,4.
- **Negativo:** acrescenta makeup, lipstick, studio portrait, text, letters, poster, picture frame, extra person e extra face.

## Transfer teste 2 (v1.1, quarto, 1 geração, ~US$ 0,06, 385 s de pod)

Métricas: identidade Luna **0,772**, semelhança com a original 0,11 (sem mistura), fundo 0%, roupa 0%, pose 0,086. Status PASS pelas métricas.

Etapas:
- Transferência: 0,416 em 23 s.
- Refino de rosto (LoRA + InstantID 0,5): 0,772 em 31 s.
- Integração: recusada, porque a identidade caiu 0,041.

**Leitura visual: ainda não passa no teste do observador.**
- **Certo:**
  - fundo idêntico, pixel a pixel;
  - rosto natural da Luna, sem maquiagem pesada;
  - cabelo castanho escuro;
  - tatuagens do peito e do braço esquerdo saíram.
- **Errado:**
  1. **Braço e mão direitos:** a mão que segurava o top sumiu e o braço novo ficou mais fino. O espaço que sobrou do braço antigo virou um "recorte" com cor de parede, e há resto de tatuagem no ombro.
  2. **Bordas do top:** uma faixa manchada (tipo estampa) em cima e embaixo. Provável causa: a detecção de tatuagem por "buraco na pele" marcou a sombra da borda do top como tatuagem, e o preenchimento virou estampa.
  3. **Cabelo:** cortado reto onde passa do contorno original (lado esquerdo).

## Transfer v1.2: fluxo do usuário (só código, sem GPU)
FOTO → máscara do ROSTO + máscara do CABELO → IDENTIDADE LUNA → BRAÇOS / MÃOS / ROUPA com a geometria original → REMOÇÃO DE TATUAGEM só na pele → INTEGRAÇÃO DE BORDAS sem tocar no rosto → RESULTADO.

- **Identidade:** só rosto, cabelo e pescoço são gerados, com LoRA, pose e denoise 0,9. Refino com InstantID 0,5 se a identidade ficar abaixo de 0,70.
- **Braços, mãos e roupa:** nenhuma passada os regenera, então a geometria é a da foto. Isso corrige o braço deformado do teste 2.
- **Tatuagem:** só na pele, longe da borda da roupa, para corrigir a faixa manchada do top. Entrada sem a tinta, denoise 0,55, sem LoRA.
- **Integração:** faixa em volta das áreas tratadas, sem o rosto, para corrigir a queda de identidade do teste 2. Denoise 0,18, sem LoRA.

## Transfer teste 3 (v1.2, quarto, 1 geração, ~US$ 0,07, 453 s de pod)

Métricas: identidade **0,751**, original 0,068, pose 0,029, fundo 0%, roupa 0%, borda 0,705. Todas as etapas foram aceitas.

**Leitura visual:**
- **Melhorou muito:**
  - rosto natural da Luna, cabelo castanho escuro, fundo idêntico;
  - top intacto, sem faixa manchada;
  - braço e mão esquerdos idênticos à foto.
- **Errado:**
  1. **Braço direito:** a detecção de tatuagem (Florence) marcou o braço e a mão inteiros. O preenchimento liso com denoise 0,55 virou uma "massinha" lisa e laranja, com contorno duro e a mão sumida.
  2. **Ombro esquerdo:** a tatuagem continua, porque a detecção não pegou.
  3. **Objeto novo:** apareceu um brinco de argola grande. E ficou uma mecha clara no cabelo do lado esquerdo.

## Transfer v1.3: spec "TESTE 4 — LUNA IDENTITY + STRUCTURE PRESERVATION" (só código, sem GPU)
- **Roupa protegida pelo Florence ("clothes"):** a tinta deixa de contar como roupa. Antes, a tatuagem do ombro caía na "roupa" e ficava intocada.
  - **Trava:** se o Florence cobrir menos de 60% da roupa vista na foto, o código volta à máscara antiga, para o top nunca virar pele.
- **Pele visível de braços, mãos, ombros e colo:** redesenhada com a **profundidade da foto original**: DepthAnything V2 no ControlNet Union, tipo depth, força 0,9 até o fim, e a tinta não aparece na profundidade.
  - Parte da foto com a tinta coberta pela cor da pele vizinha (raio pequeno, mantém luz e sombra). Denoise 0,7, sem LoRA.
  - **Teste local na foto real:** a tinta detectada cobre as tatuagens do ombro, do braço e da mão.
- **Integração:** não toca no rosto nem no top, que fica exato.
- **Negativo:** a lista do usuário (80 termos: mãos deformadas, tatuagem fantasma, brinco de argola, joias novas, top alterado, fundo alterado etc.).
- **Rosto:** mesma semente e mesmas máscaras do teste 3. O negativo novo pode mudar um pouco o rosto, e o mínimo de identidade continua 0,70.
