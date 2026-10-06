# V2: modo "replicar foto". Rodada 1 com as 5 fotos do usuário (2026-10-06)

- **Decisões do usuário:**
  - trocar a pessoa e manter a foto;
  - cabelo sempre o da Luna;
  - sem tatuagens;
  - óculos escuros mantidos;
  - fotos só para teste.
- **Pipeline:**
  1. leitura da foto (LunaFaces, DWPose, Florence-2 PromptGen e luz medida);
  2. troca da pessoa (SAM2 + RealVisXL/LoRA, denoise 0,65);
  3. cabelo (0,80) e braços (0,55);
  4. rosto 1 (InstantID), rosto 2 e rosto 3 (sem LoRA);
  5. corpo 1 e corpo 2;
  6. conferência.
- **Custo:** ~US$ 0,33, com 33 min + 2 min para buscar os resultados. O teto autorizado era US$ 0,35.
- **Dados:** `docs/testes/bench_v2_replicar_rodada1.json`.
- **Imagens:** `generated/v2/replicar/` (original × Luna e a folha `00_original_x_luna.jpg`).

| Foto | Rosto | Idade | Pose × foto | Fundo alterado | Tentativas | Tempo | Observação visual |
|---|---|---|---|---|---|---|---|
| 1 quarto (top preto) | 0,782 | 28 | 0,074 | 0,0% | 1 | 354 s | Ótima: mesma pose e fundo, braços sem tatuagem, cabelo escuro |
| 2 braço na cabeça | 0,790 | 26 | — | 0,04% | 1 | 238 s | Pose e fundo certos; **roupa mudou** (regata de renda virou blusa com botões) |
| 3 rua (conjunto bege) | 0,715 | 24 | 0,051 | 0,0% | 1 | 235 s | Fundo e pose ok; **roupa mudou** (top tomara-que-caia virou colete) |
| 4 carro (selfie) | 0,739 | 29 | — | 0,0% | 2 (1ª: 0,632) | 457 s | **Cabelo continuou loiro**: a passada de cabelo foi desfeita (pose 0,066 > 0,05). A mão no cabelo sumiu |
| 5 varanda (óculos) | 0,776 | 23 | 0,040 | 0,0% | 1 | 281 s | Cabelo trocado, fundo perfeito; **óculos sumiram** e o corset virou uma blusinha |

**O que funcionou**
- **Fundo intacto em 5/5:** no máximo 0,04% dos pixels fora da pessoa mudaram.
- **Pose preservada:** distância ≤ 0,074.
- **Rosto:** de 0,72 a 0,79 em 5/5, todos acima de 0,70, com uma refeita na foto do carro.
- **Idade e tatuagens:** idade de 23 a 29, e nenhuma tatuagem.

**Problemas e correções (rodada 2, commitadas)**
1. **Organização da leitura pelo Qwen falhava** (`chat` devolve texto e modelo): por isso a roupa não entrava organizada no prompt. Corrigido. O Qwen também sai da GPU logo depois, porque a VRAM chegou a 23,8 de 24 GB com ele carregado.
2. **Roupa mudando:** a troca base caiu de 0,65 para 0,50, e o prompt passou a usar a roupa lida.
3. **Cabelo loiro mantido:** a passada de cabelo aceita mudança de pose até 0,15 (a tolerância geral é 0,05) e ficou mais forte (0,85).
4. **Óculos escuros sumindo:** quando a foto tem óculos, os pixels originais deles voltam no fim e o rosto é medido de novo.
5. **Expressão:** no modo replicar não entra a expressão padrão da Luna; vale a da foto.
6. **Câmera:** "front camera" na descrição conta como selfie. A foto 1 tinha sido classificada como "outra pessoa".

**Ainda não medido:** fidelidade da roupa e de objetos na mão (só a olho), anatomia das mãos, e a cor da pele em relação à luz da foto.
