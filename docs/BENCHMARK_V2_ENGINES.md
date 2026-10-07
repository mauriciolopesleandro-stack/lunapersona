# Benchmark das engines V2 (Replacement)

Branch `feature/persona-v2-hyperreal-replacement`. Scripts: `scripts/v2_engine/`.
- `sessao_v2.py` roda o plano no pod.
- `rodar_pod.sh` baixa os modelos no disco temporário, roda e limpa (sem tocar em arquivo do volume).
- `coletar_v2.py` junta as imagens.

Imagens em `generated/v2/engines/` (fora do git).

## Smoke test, 2026-10-07

**Configuração**

| | |
|---|---|
| Pod | `fj609he9jnc8e6` (EU-RO-1; criado pelo failover, porque o host do pod anterior estava sem GPU) |
| GPU | NVIDIA RTX PRO 4000 Blackwell, 24 GB, driver 580.159.04, CUDA 13.0 |
| ComfyUI | 0.30.0 |
| Preço | US$ 0,57/h |
| Modelo | RealVisXL V5 fp16 (sha 6a35a785…, confere com o registro) |
| LoRA | lunavox_sdxl_v1, força 1.0 (sha 0a58a72e…) |
| ControlNet | Union promax (9fae2e50…) |
| InstantID | c8127be9… / 02b3618e… |
| Workflow | `sdxl-inpaint-control` |
| Foto e modo | quarto (1_quarto_top_preto), modo QUALITY, semente 7801, sem nova tentativa |

**Tempo e custo**

| | |
|---|---|
| Downloads dos 4 modelos | ~70 s (13,6 GB, HF oficial) |
| Job | 299 s de parede; 108 s de GPU nas passadas |
| Pico de VRAM | 10,8 GB |
| Pod ligado | 8,1 min, **~US$ 0,08** |

**Medidas da validação (na época)**

| Medida | Valor | Status |
|---|---|---|
| Identidade | 0,775 | PASS |
| Pose | 0,023 | PASS |
| Fundo | 0,0 | PASS |
| Tatuagem | 0,123 | WARN |
| Rosto original | **não medido** | não apareceu como falha |
| Resultado geral | | **WARN** |

**Olhando a imagem: NÃO aprovada.**

| Etapa | O que fez |
|---|---|
| Passe de identidade | **muito bom**: rosto natural, sardas, cabelo crível, sem emenda |
| Refino de rosto (InstantID com a master) | deixou o rosto "maquiado" (sobrancelha marcada, contorno), porque copia a maquiagem da referência |
| Limpeza de tatuagem | pintou pele por cima dos fios de cabelo no ombro esquerdo; **quadrados cinza** no braço direito; parte da tinta ficou |
| Integração | clareou cabelo e colo com **borda dura** (cabelo recortado na parede, mancha no colo). O degrau pele × pele (+21) era aplicado na borda inteira da região, cabelo × parede inclusive, e a harmonia de tom usava máscara sem transição |

Medidas novas, calibradas nessas imagens (preliminar, 1 foto):

| Etapa | Emenda | Bordas retas |
|---|---|---|
| identidade | 5,1 | 6,4 |
| refino de rosto | 5,9 | 8,4 |
| tatuagem | 4,8 | **13,3** |
| final | **11,2** | **13,3** |

Limites: emenda PASS ≤ 7 e REJECT > 10; bordas retas PASS ≤ 9 e REJECT > 12.

**Correções (commit cc0e9ba, sem GPU)**
- Validação: nova dimensão `seams` (emenda + blocos). "Rosto original não medido" agora é WARN, não passa calado.
- Integração: o degrau só entra na pele perto da fronteira pele × pele, limitado a 12. A harmonia rosto × corpo tem transição suave e limite de 8.
  - Na imagem do smoke test, a emenda caiu de 5,9 para 5,0, sem mexer no cabelo.
- Engine:
  - Refino de rosto, limpeza de tatuagem e integração só ficam se não criarem emenda nem bloco novo; senão, voltam à imagem anterior (registrado na telemetria).
  - Refino com referência só fica com ganho de identidade ≥ 0,02 (fora do MAX_QUALITY).
  - Cabelo e região da identidade ficam fora da zona de tatuagem.
  - Recorte do rosto original com margem de 60% (o recorte apertado fazia o detector falhar).
  - Máscaras e identidade por etapa vão para as intermediárias e a telemetria.

**Achado real que a imagem mostra:** a pele gerada da Luna saiu ~33 níveis mais escura que a pele original do corpo. É o bronzeado das referências. A integração não "pinta por cima"; a validação aponta.

## Reteste, 2026-10-07 (QUALITY, quarto + varanda; autorizado até US$ 0,15)

| | |
|---|---|
| Pod | `8pyb19faz46ezj` (novo pelo failover), RTX PRO 4000 Blackwell |
| Tempo e custo | 13,8 min, **~US$ 0,13** |
| Downloads | 200 s desta vez (no smoke foram 70 s) |

Os dois resultados foram **REJECT**, e a validação agora diz o porquê, por dimensão.

### Quarto

| Etapa | Identidade | Resultado |
|---|---|---|
| Passe de identidade | 0,325 | O rosto "natural" do passe 1 **não é a Luna** |
| Refino com InstantID | 0,781 | **Descartado** pela trava nova por uma emenda 5,0 → 6,1, que ainda estava dentro do PASS. Erro da trava, corrigido: só derruba quando passa do limite aceitável |
| Tatuagem | — | Descartada corretamente (blocos 5,3 → 11,0). O modelo vira a mão "massinha", e o ajuste de tom faz quadrados |
| Final | | Fica a tinta original (0,45), sem estragar a imagem |

Rosto original agora **medido**: 0,238.

### Varanda (com óculos escuros)

- **Identidade 0,03.**
- O buraco dos óculos na máscara partia o rosto em dois pedaços pequenos, e o modelo gerou um rosto escuro e incoerente.
- **Corrigido:** a máscara cobre os óculos, e os pixels originais deles voltam colados por cima depois de cada passe e no final. Regra do usuário: óculos mantidos.

### Conclusões

- A trava visual funciona: nenhuma emenda nem quadrado chegou ao final do quarto.
- A tatuagem em mão e braço continua sem solução com inpaint SDXL. É limitação conhecida (testes 1 a 7) e está documentada.

## Teste da tatuagem, foto do usuário (2026-10-07, autorizado)

**Configuração:**

| | |
|---|---|
| Pod | `qkcnzi7plh3ukd`, RTX PRO 4000 Blackwell |
| Tempo e custo | 13 min, **~US$ 0,12** |
| Modo | QUALITY + política de atributos (spec 45), 1 nova tentativa |
| Pedido | remove `tattoos`; preserve pose/roupa/cenário/luz/enquadramento; reconstruct rosto/corpo/pele |

**Resultado (1ª tentativa, a que ficou): WARN**

| Medida | Valor | Observação |
|---|---|---|
| Identidade | 0,725 | passa |
| Rosto original | 0,021 | nada da pessoa original |
| Tatuagem | 0,114 | antes 0,45 |
| Pose | 0,027 | |
| Roupa alterada | 0 | |
| Emenda | 3,9 | |
| Bordas retas | 9,2 | WARN |

**No olho:**
- Funcionou:
  - Tatuagens do ombro e do braço **saíram** (pele reconstruída com a LoRA, sem mancha no colo nem cabelo recortado).
  - Rosto natural da Luna.
- Defeito 1: a **mão direita virou "luva"**. Os dedos que seguram o top sumiram. Causa: a entrada lisa (push-pull) apagou o sombreado dos dedos, e a profundidade calculada nela virou um bloco.
- Defeito 2: **remendos retangulares** claros e fracos no braço esquerdo, no ombro e na barriga. Causa: manchinhas (pintas e poros) entraram na máscara de marcas, e a dilatação quadrada as transformou em quadrados que o refino e a integração clarearam.
- Defeito 3: a nova tentativa atacou o **fundo** (0,0031, quase no limite) em vez da tatuagem, e piorou o rosto (0,35). Ficou a 1ª.

**Correções (sem GPU):**
- Entrada que **preserva a anatomia**: um fechamento em tons de cinza tira o traço fino e mantém as juntas e os dedos; a tinta cheia vai para o push-pull. Conferido no recorte real da mão: o punho fechado continua.
- Máscara de marcas sem manchinhas isoladas e com dilatação **redonda**.
- Denoise da pele 0,75.
- A nova tentativa ataca o aviso mais grave (gravidade dentro da faixa), não o primeiro da lista.

## Teste da mão, 2 versões na mesma ligação (2026-10-07)

| | |
|---|---|
| Pod | `qkcnzi7plh3ukd` (religado) |
| Tempo e custo | 16,5 min, **~US$ 0,16** (inclui ~3 min perdidos por dois erros meus na partida: ComfyUI ainda montando os nós e o script com final de linha CRLF) |

| Versão | Identidade | Tatuagem | Resultado |
|---|---|---|---|
| denoise **0,6** | 0,73 | 0,11 | **mão certa:** punho, juntas e dedos segurando o top; a rosa saiu |
| denoise 0,75 | 0,73 | 0,16 | REJECT (mais tinta, emendas) |

Defeito que sobrou na 0,6: manchas claras redondas, fracas, no braço esquerdo e na barriga.
- **Causa:** o refino leve em anel + integração, aplicados depois do modelo. A saída do modelo estava limpa.
- **Correção (commit 72ba342):** o anel foi trocado por um casamento de cor de baixa frequência (push-pull). Ele só age onde há pele limpa perto e tem limite de 8. Denoise da pele fixado em 0,6.
- **Prévia (saída real da GPU + correção em CPU):** sem manchas, mãos intactas.

**Ainda sobra:** dois pedacinhos de tatuagem nas bordas protegidas: junto ao cabelo, no ombro esquerdo, e junto ao top, no antebraço direito.

## Versão final, foto do usuário (2026-10-07, autorizado)

| | |
|---|---|
| Tempo e custo | 8,5 min, **~US$ 0,08** |
| Pipeline | pele 0,6 + casamento de cor + máscara completada até a borda do top |

| Medida | Valor |
|---|---|
| Identidade | 0,727 |
| Rosto original | 0,021 |
| Pose | 0,012 |
| Fundo | 0,0 |

**Na GPU:**
- **Braço e mão direitos:** limpos. Punho e dedos intactos, a rosa saiu, e o pedaço junto ao top também saiu (sobrou um pontinho).
- **Braço esquerdo:** mancha clara com borda no vinco da axila. A causa era a integração FINAL, que aplicava o degrau pele × pele (+13) na borda das zonas de marcas.

**Correção (commit 50381b8):** a integração final só age em volta da identidade. O final foi recomputado em CPU com as imagens desta rodada (etapa determinística): a mancha sumiu e a emenda caiu de 6,9 para 5,5.

**Limitação que fica:** um pedaço da flor no ombro esquerdo. Fica em pele na sombra, junto à cadeira escura; ali a tinta não é mais escura que a pele em volta, e a regra de cor não separa os dois com segurança.

`scripts/v2_engine/plano_escada.json`: 8 degraus × 2 fotos (quarto, varanda), semente 7801, só RealVisXL. Depois, 3 sementes na melhor configuração e uma geração V1 de regressão.


## Varanda com óculos, versão final (2026-10-07, autorizado)

| | |
|---|---|
| Tempo e custo | 8 min, **~US$ 0,08** |
| Resultado | **REJECT** |

| Medida | Valor |
|---|---|
| Identidade | 0,30 |
| Rosto original | 0,28 |
| Tatuagem | 0,32 |
| Pose | 0,009 |
| Fundo | 0,0 |

**Melhorou:**
- O rosto saiu **inteiro e coerente**: o buraco dos óculos foi resolvido.
- Cabelo castanho da Luna, óculos mantidos (PRESERVE), cenário intacto.

**Falhas e causas:**

| Falha | Causa |
|---|---|
| Identidade 0,30 | O refino com referência chegou a 0,675, mas a trava visual o descartou: trocar cabelo loiro por escuro contra o céu contava como "emenda" |
| Argolas da pessoa original ficaram | Penduradas fora da máscara do rosto (política REMOVE não aplicada) |
| Pulseira virou bloco branco | O preenchimento só tirava tinta escura |
| Grão pesado no rosto | Integração adicionou 5,6, medido na pele ao sol do ombro |
| Tatuagens finas dos braços não detectadas | Traços finos em pele bronzeada (limitação) |

**Correções:**
- A borda contra o fundo não conta como emenda. Na própria rodada, o refino passaria: emenda 3,7, blocos 8,8 < 11,9, identidade 0,675.
- Brinco/colar REMOVE perto da cabeça entra na geração do rosto.
- Joia removida no corpo é preenchida inteira.
- Teto de grão 2,5.

**Pendente:** detector de tatuagem fina em pele bronzeada.
