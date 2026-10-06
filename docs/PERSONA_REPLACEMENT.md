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
