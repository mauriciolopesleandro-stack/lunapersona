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

---

# Teste 2: multi-pass RealVisXL (2026-10-06)

O usuário pediu 3 cenas: academia, selfie no espelho do quarto e toalha na cabeça se maquiando. Cada uma com RealVisXL + LoRA, 3 passadas de rosto e 2 de corpo.

- **Limite:** padrão da tarefa, US$ 0,10.
- **Trava:** desligou o pod aos 10 min.
- **Resultado:** **só a academia terminou.** O pod novo levou ~2 min para liberar o SSH, e cada passada foi ~3× mais lenta que o estimado. A selfie parou no meio e a toalha não começou.
- **Gasto total:** ~US$ 0,10, contando ligar o pod de novo ~1,5 min só para buscar os resultados no volume.
- **Dados:** `docs/testes/bench_v2_multipass_academia.json`.
- **Folha:** `docs/testes/v2_multipass_academia.jpg` (fora do git).

| Passada | denoise / strength | Rosto | Idade | Pele (telemetria) | Pose (dist. da base) | Decisão | Tempo |
|---|---|---|---|---|---|---|---|
| base | 1.0 / — | 0,297 | 30 | 0,96 | 0 | — | 27,7 s |
| rosto 1 (estrutura) | 0,30 / 0,75 | **0,450** | 29 | 0,65 | 0,004 | aceita | 36,0 s |
| rosto 2 (refino) | 0,20 / 0,42 | 0,477 | 29 | 0,61 | 0,004 | aceita | 31,6 s |
| rosto 3 (microdetalhe) | 0,12 / 0,22 | 0,482 | 29 | 0,60 | 0,005 | aceita | 30,7 s |
| corpo 1 (estrutura) | 0,25 / 0,60 | 0,486 | 29 | 0,61 | 0,012 | aceita | 30,1 s |
| corpo 2 (refino) | 0,15 / 0,30 | 0,486 | 29 | 0,61 | 0,011 | aceita | 32,9 s |

Validação final: FAIL `face_identity_low` (0,486 < 0,55). Pessoas: PASS. Anatomia: UNKNOWN. Corpo: NOT_COMPARABLE.

## Leitura

- **Identidade:** subiu de 0,30 para 0,49 (+0,19). Quase tudo veio da 1ª passada de rosto (+0,15); as passadas 2 e 3 somaram +0,03, e as de corpo não mexem no rosto. Ainda fica abaixo do limiar de 0,55.
- **Preservação:** pose, roupa, cenário e cabelo continuaram iguais (pose ≤ 0,012). Nenhuma passada precisou de rollback.
- **Pele, no olho:** fotográfica, mas com maquiagem marcada (sobrancelha e boca). Fica entre a base do teste 1 e a V1.
- **Fidelidade ao pedido:** em vez de segurar a garrafa de água, ela segura um halter. Isso vem da imagem base.
- **Tempo:** 214 s por imagem, contra os ~80 s estimados. Cada passada levou ~21 s no ComfyUI, porque a análise de rosto e corpo entre as passadas tira o checkpoint SDXL da memória e ele recarrega toda vez (cache clássico do ComfyUI).
- **Custo:** ~US$ 0,034 por imagem.

## Pendências

- **Cenas que faltam:** selfie no espelho e toalha. Faltam ~US$ 0,07 para as duas, nesta velocidade.
- **Velocidade:** a análise precisa sair do ComfyUI, ou o ComfyUI precisa de cache maior (`--cache-lru`, o que muda os argumentos do pod). Qualquer uma das duas exige autorização.
- **Identidade abaixo de 0,55:** as opções são mais denoise ou um recorte maior na 1ª passada de rosto (experimento separado), ou um adaptador de identidade.

---

# Teste 3: realismo da referência + velocidade (2026-10-06)

O alvo de realismo é a selfie de celular que o usuário mandou, gerada com RealVisXL. Está em `personas/luna/style_refs/realismo_ref_01.webp`, fora do git, com sha256 `63800d9f…`.

**Mudanças**
- **Perfil de estilo `smartphone_raw_v1`:**
  - texto positivo: selfie de celular crua, sardas leves, poros, pouca maquiagem, fios soltos, luz de janela, cores levemente lavadas;
  - texto negativo: maquiagem pesada, contorno, batom, pele retocada, luz de estúdio.
- **Passadas de rosto:**
  - a 1ª ficou mais forte (denoise 0,42, opacidade 0,85, recorte mais próximo);
  - a de microdetalhe passou a rodar **sem a LoRA**, só para devolver textura.
- **Velocidade:** a análise de rosto e corpo passou a manter o checkpoint no cache do ComfyUI.

**Rodada:** ~US$ 0,065, pod desligado aos 8 min 11 s. A academia e a selfie terminaram. A toalha ficou de fora para caber no limite, e eu desliguei o pod antes da trava.

| Cena | Base | Rosto 1 | Rosto 2 | Rosto 3 | Corpo 1 | Corpo 2 | Final | Idade final | Tempo |
|---|---|---|---|---|---|---|---|---|---|
| academia | 0,300 | 0,399 | 0,412 | 0,424 | rollback (rosto novo apareceu) | 0,424 | **0,424** | 31 | 172 s |
| selfie | 0,371 | 0,507 | 0,494 | 0,493 | 0,498 | 0,498 | **0,498** | 31 | 148 s |

**Leitura**
- **Realismo: chegou perto da referência.** A pele ficou fosca, com sardas e poros, sem a maquiagem marcada, com fios soltos e luz natural. É bem diferente da academia do teste 2.
- **Identidade:** caiu um pouco em relação ao teste 2 (0,42–0,50 contra 0,49). O estilo afasta do "visual produzido" que a LoRA aprendeu. Continua abaixo de 0,55.
- **Idade:** ~31. O estilo cru envelhece um pouco, e a 1ª passada chegou a ~35 antes de voltar.
- **Velocidade:** com o modelo no cache, cada passada caiu de ~21 s para ~11 s no ComfyUI. O gargalo agora é a análise entre as passadas (~10–15 s cada).
- **Fidelidade ao pedido:**
  - a "selfie no espelho" saiu como foto de frente segurando o celular, sem espelho;
  - na academia ela segura halteres em vez da garrafa.
- **Guarda:** desfez a passada de corpo 1 da academia, que fez aparecer um segundo rosto.

**Próximos passos possíveis (cada um com autorização)**
1. **Identidade ≥ 0,55 sem perder o estilo:**
   - InstantID só na 1ª passada de rosto, seguido da passada sem LoRA para devolver a textura;
   - ou mais denoise na 1ª passada.
2. **Velocidade:** não rodar o DWPose nas passadas de rosto. A máscara de rosto não alcança o corpo, então a pose não tem como mudar.
3. **Estilo:** manter `smartphone_raw_v1` como padrão da V2, que é a trava pedida pelo usuário.

---

# Teste 4: InstantID na 1ª passada de rosto + estilo da referência (2026-10-06)

- **Cena:** selfie no supermercado, pós-treino, com o carrinho cheio de compras variadas e suor visível. Duas sementes: 7311 e 7312.
- **Pipeline:** RealVisXL + LoRA, base, rosto 1 (**InstantID** com a master_face, peso 0,8, denoise 0,42), rosto 2 (com LoRA), rosto 3 (sem LoRA, para a textura), corpo 1 e corpo 2.
- **Custo:** ~US$ 0,083; o pod ficou 8 min 45 s ligado.
- **Download:** os modelos do InstantID (4,2 GB) e o RealVisXL foram baixados no disco temporário do pod em ~67 s e apagados no fim.

| Variação | Base | Rosto 1 (InstantID) | Rosto 2 | Rosto 3 (sem LoRA) | Corpo 1 | Corpo 2 = final | Idade final | Validação final | Tempo |
|---|---|---|---|---|---|---|---|---|---|
| A (7311) | 0,265 | **0,798** | 0,795 | 0,792 | 0,793 | **0,793** | **27** | **PASS_WITH_UNKNOWN** (anatomia sem detector) | 192 s |
| B (7312) | 0,406 | **0,827** | 0,817 | 0,803 | 0,800 | **0,800** | 25 | **PASS_WITH_UNKNOWN** | 155 s |

**Leitura**
- **Identidade:** passou do limiar com folga. A média de 0,80 equivale à V1 (0,81–0,85 com o Qwen). O ganho veio todo da passada com InstantID; as seguintes perderam no máximo 0,02. Pose ≤ 0,011 e uma pessoa só.
- **Pele:** continuou natural, com sardas, bochechas coradas, pouca maquiagem e textura. O InstantID alisou um pouco em relação à base, e a passada sem LoRA devolveu parte da textura.
- **Idade:** chegou a 25–27, no alvo. As rodadas sem InstantID ficavam em ~30–35.
- **Fidelidade ao pedido:**
  - o carrinho aparece com bananas (A) ou com pouco do conteúdo visível (B), e não com "compras variadas";
  - quase não se vê suor;
  - ela empurra o carrinho olhando para a câmera, em vez de segurar as bananas numa selfie de braço esticado.
- **Validação:** as duas passam na validação final (rosto, pessoas, idade). Anatomia continua sem detector.

**Conclusão parcial:** RealVisXL + LoRA + estilo `smartphone_raw_v1` + InstantID na 1ª passada de rosto é a primeira configuração da V2 que junta **identidade de nível V1** com **pele natural**. Ainda não foi comparada lado a lado com a V1 nas mesmas cenas, e a amostra é de 2 imagens.
