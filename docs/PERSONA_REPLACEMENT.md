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
