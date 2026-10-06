# Persona Engine V2: teste 1 (base), só RealVisXL

Data: 2026-10-06.
Branch: `feature/persona-v2-realvis-lustify`. **NÃO publicado na main. A V1 não foi alterada.**
Autorização do usuário: só RealVisXL, 3 imagens, sementes 7101/7103/7106, sem Lustify e sem multi-pass. O teto passou de US$ 0,06 para US$ 0,09 depois que a medição mostrou que a LoRA levaria ~4 min para subir.
Dados brutos: `docs/testes/bench_v2_base_realvis.json`.
Folha de comparação: `docs/testes/bench_v2_realvis_vs_v1.jpg`. Está fora do git, porque o `.gitignore` ignora `*.jpg`.

## Configuração

- **Modelo:** RealVisXL V5.0 fp16. O sha256 conferido no pod foi `6a35a785…`.
- **LoRA:** `lunavox_sdxl_v1` com peso 1.0, só no UNet. É a LoRA SDXL da Luna, treinada sobre o RealVisXL V5; a `luna_zimage_v1` da V1 não serve em SDXL.
- **Geração:** 832×1216, 30 passos, CFG 5, dpmpp_2m_sde/karras.
- **Negativo:** aplicado de verdade, porque o CFG é 5. Junta o negativo global, o da persona, termos de pele plástica e termos para evitar nudez.
- **Prompt:** o mesmo do PromptBuilder da V1, com as mesmas cenas e sementes das imagens A do benchmark V1.1.
- **O que não roda:** Qwen, correção e pós-processamento.

## Resultado

| Cena (semente) | Versão | Rosto (ArcFace × master) | Pele (telemetria) | Idade estimada | Pessoas | Corpo | Anatomia | Geração |
|---|---|---|---|---|---|---|---|---|
| Cafeteria, corpo inteiro (7101) | **V2 RealVisXL** | **0,310** | 0,18 | 31 | PASS | NOT_COMPARABLE | UNKNOWN | 15,2 s |
| | V1 | 0,822 | 0,75 | 26 | PASS | NOT_COMPARABLE | UNKNOWN | ~131 s/img (lote) |
| Close na janela, Rio (7103) | **V2 RealVisXL** | **0,446** | 0,40 | 30 | PASS | NOT_COMPARABLE | UNKNOWN | 13,7 s |
| | V1 | 0,866 | 0,82 | 26 | PASS | NOT_COMPARABLE | UNKNOWN | |
| Sentada no Ibirapuera (7106) | **V2 RealVisXL** | **0,367** | 0,82 | 29 | PASS | NOT_COMPARABLE | UNKNOWN | 13,7 s |
| | V1 | 0,852 | 0,75 | 22 | PASS | NOT_COMPARABLE | UNKNOWN | |
| **Média** | **V2** | **0,374** | 0,47 | 30,0 | 3/3 | — | — | **14,2 s** |
| | V1 | 0,847 | 0,77 | 24,7 | 3/3 | — | — | ~131 s |

- **Validação:** as 3 imagens da V2 reprovaram em `face_identity_low`, porque o limiar é 0,55.
- **Recursos:** 7,6 GB de VRAM no pico, contra 21,8 GB da V1.
- **Custo de geração por imagem:** ~US$ 0,002 na V2, contra ~US$ 0,02 da V1.
- **Custo do teste:** US$ 0,071 no total, contando ligar o pod, subir a LoRA de 170 MB e baixar o checkpoint de 6,9 GB.

### Leitura visual (folha de comparação)

- **Pele e realismo: V2 muito melhor.**
  - A pele é fosca, com textura de foto, quase sem maquiagem e com variação de tom, e parece uma pessoa fotografada.
  - A V1 tem o aspecto já conhecido de pele lisa, brilhante e maquiada.
- **Idade: V2 mais próxima ou acima do alvo de 27.** A V2 parece ter ~28–31 anos; a V1, 22–26.
- **Identidade: V2 bem pior.** Cabelo escuro ondulado e gargantilha batem com a Luna, mas o rosto é de outra mulher parecida. O ArcFace confirma, com média de 0,37 contra 0,85.
- **Fidelidade ao pedido: V1 melhor.**
  - Na cafeteria, a V2 não segura a xícara.
  - O "close-up" saiu como plano médio.
  - O "denim shorts" saiu como calça jeans.
- **Anatomia e cenário:** sem defeito visível nas 3 imagens. O cenário é coerente: Pão de Açúcar, parque e cafeteria.

A nota da pele contradisse o olho mais uma vez. A imagem 7101 da V2 tem pele visivelmente natural e tirou 0,18. Continua só como telemetria.

## Decisão

- **Sobre o modelo:** o RealVisXL base resolve o problema que motivou a V2, a pele plástica, mas **perde a identidade**. Só com a LoRA, a média ficou em 0,37, abaixo do limiar de 0,55. Sem um passo de identidade, ele não serve.
- **Sobre o multi-pass:** a hipótese do multi-pass, "construir a identidade em passadas", é exatamente o que falta medir. A distância a vencer é grande: de 0,37 até pelo menos 0,55.
- **Lustify:** não foi testado, por decisão do usuário. A comparação RealVisXL × Lustify continua em aberto.
- **Próximo passo:** depende de autorização. É o teste 2 (multi-pass) com o RealVisXL, ou primeiro o Lustify, pelo mesmo script.

## Limitações

- **Amostra:** são só 3 imagens, sem retry e só em modo livre.
- **Identidade:** é medida só por ArcFace contra a master_face. A semelhança de cabelo e da gargantilha não é medida.
- **Anatomia:** fica UNKNOWN, porque não há detector.
- **Corpo:** fica NOT_COMPARABLE, porque nenhuma cena está na pose da master_body.
- **Pele:** a nota confunde brilho com textura.
- **Aparência CGI, porcelana e simetria:** não são medidas.
- **Fidelidade ao prompt:** roupa, objeto na mão e enquadramento não são medidos automaticamente.
- **Tempo de geração:** os 13,7–15,2 s incluem a carga do checkpoint na primeira imagem. Validação e análise de rosto e corpo não estão nesse número.
