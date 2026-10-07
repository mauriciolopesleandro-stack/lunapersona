# Benchmark de troca de cabeça: sessão única (2026-10-07)

`PROMPT_modelo_fase_unica.md`. Código em `scripts/bench_cabeca/`. Imagens em `generated/v2/bench_cabeca/`. Tabela completa em `docs/testes/bench_cabeca_tabela.json`.

## Sessão
- **Pod:** `luna-bench` (RTX PRO 4500 32 GB, US$ 0,72/h, EU-RO-1, disco de 160 GB), criado pelo estúdio, que se desligou na hora. Desligou sozinho no fim (`/fim`).
- **Tempo:** **~42 min de pod** (≈ US$ 0,50), mais ~4 min do estúdio para criar o pod (≈ US$ 0,04). Total ≈ **US$ 0,54**.
  - Cerca de 13 min foram perdidos por erro meu: o proxy da RunPod (Cloudflare) recusa o user-agent padrão do Python (403), e a sessão ficou esperando sem avisar. Corrigido em `rodar_sessao.py`.
- **Downloads:** **88,7 GB**, em paralelo. Q3 em 1 min, Qwen-Image-2.1 em 4 min, FireRed 41 GB em 10 min, NVFP4 em 22 min.
- **Quedas:** 0. As 42 execuções terminaram e o ComfyUI v0.37 separado não caiu nenhuma vez.
- **RAM:** o pico medido pelo `free` dentro do contêiner é o do host (90 a 135 GB), não o do pod. Não é confiável e fica fora da decisão.

## Resultado por variante (semelhança na final composta, média de 4 referências da Luna, antelopev2)
| Variante | c05 (1234/5678) | c09 | c14 | média | tempo/imagem* | VRAM pico | checagem |
|---|---|---|---|---|---|---|---|
| **B: 2511 fp8 (estúdio)** | 0,657 / 0,640 | 0,688 / 0,697 | 0,855 / 0,823 | **0,727** | 22 s | 29,8 GB | 4/6 (2× emenda) |
| G: fp8 + LoRA da Luna | 0,621 / 0,673 | 0,698 / 0,731 | 0,786 / 0,815 | 0,721 | 22 s | 29,9 GB | 2/6 (emenda) |
| A: 2511 Q3_K_M | 0,646 / 0,653 | 0,640 / 0,644 | 0,822 / 0,806 | 0,702 | 37 s | 28,3 GB | 3/6 (emenda) |
| C: 2511 NVFP4 | 0,555 / 0,666 | 0,583 / 0,669 | 0,838 / 0,843 | 0,692 | 56 s | 32,0 GB | 4/6 (emenda) |
| F: Qwen-Image-2.1 (pesquisa) | 0,891 / 0,878 | 0,621 / 0,707 | 0,833 / 0,869 | **0,800** | 44 s | 27,3 GB | 6/6 |
| D: FireRed 1.1 + BFS | 0,551 / 0,643 | 0,530 / 0,524 | **falhou** | 0,562 | 22 s | 29,9 GB | 4/6 |
| E: FireRed 1.1 | 0,588 / 0,602 | 0,479 / 0,489 | **falhou** | 0,539 | 22 s | 30,0 GB | 4/6 |

\*Mediana, sem o primeiro carregamento do modelo: 95 a 153 s.

## Leitura visual (`grade_c05/09/14.jpg`, `rec100_*`)
- **B, G, A e C:** a Luna aparece de forma muito parecida em todas, com rosto, cabelo longo castanho e luz da cena. A 100%, o B tem o tom de pele mais natural. O G fica mais contrastado, com um leve halo claro no cabelo da cena 9.
- **F:** rosto mais "Luna" na cena 5, mas cabelo mais curto. Na cena 5 sobrou um fiapo claro do cabelo original na borda.
- **D e E (FireRed):** rosto mais genérico, com coque. **Na cena 14 devolveram o retrato de referência no lugar da cena** (`firered_c14_cru.jpg`). O alinhamento reprovou certo.
- **Emenda:** as reprovações de "emenda" (ΔE de 4,0 a 5,0) não aparecem a 100%. A medida compara a metade externa do degradê com a original, e ali o cabelo novo, longo, legitimamente cobre o fundo. A métrica precisa ser recalibrada para medir o degrau de cor **através** da borda.

## Decisão (regras do prompt)
1. **Eliminadas:**
   - **D e E:** caíram no alinhamento e ignoraram a cena na c14; semelhança 0,54 a 0,56.
   - **F:** não pode ir para produção.
   - Pela letra da checagem, todas as outras também têm uma reprovação de emenda. Visualmente são falsos positivos (ver acima).
2. **Vencedora: B (2511 fp8, o do estúdio).**
   - **Por quê:** maior semelhança entre as que podem ir para produção (0,727) e a mais rápida (22 s).
   - **G (+ LoRA da Luna):** empata (0,721, diferença dentro do ruído das sementes) e não melhora.
   - **A (Q3):** perde 0,025 e é 1,7× mais lento.
3. **NVFP4 (C):** **não substitui o fp8.** Perde 0,035 (limite 0,02) e é 2,5× mais lento, porque não há aceleração sem PyTorch cu130 (o pod usa cu128).
4. **Qwen-Image-2.1 (F):** **ganha com folga** (0,800 contra 0,727). Só o usuário decide sobre pedir a licença comercial.
5. **Cena de massagem:** não rodou, por decisão do usuário. Sem conclusão sobre "cena, modelo ou referência".

## Observação
**Argola:** a referência principal da Luna (`f7e81b9a…`, `retrato_frontal_1`) usa argola grande, e todas as variantes a reproduzem. Para tirar a argola das trocas, a troca de referência é mais eficaz que o negativo.

## Teste com a foto do quarto (2026-10-07, ~10 min de pod do estúdio, ≈ US$ 0,10)
Fluxo completo:
1. **Cabeça:** variante B (2511 fp8 + BFS), 2 sementes, com `compor_cabeca`. Semelhança **0,750 / 0,749** na final. Nada mudou fora da cabeça.
2. **Tatuagens:** retoque com o ombro e o braço em denoise 0,65 e a mão em recorte próprio com denoise 0,45. **REPROVADO:** tom ΔE 5,6, blocos 0,010, pontinhos 15.

**Leitura visual:**
- **Cabeça:** a Luna clara, com cabelo longo e luz coerente. Mas o rosto ficou **muito maquiado e mais bronzeado que o corpo** (o estilo da referência) e **com argola nas duas orelhas** (da referência).
- **Corpo:**
  - **ombro esquerdo:** uma mancha cinza na borda;
  - **peito, perto do braço:** riscos escuros, o "fantasma" da tatuagem;
  - **mão:** ainda com aspecto de massa.
