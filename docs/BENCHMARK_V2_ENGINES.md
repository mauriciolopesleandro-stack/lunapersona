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


## Duas fotos sem tatuagem (porta / espelho com celular), 2026-10-07

| | |
|---|---|
| Tempo e custo | 14 min, **~US$ 0,14** |

| Foto | Identidade | Rosto original | Pose | Fundo | Validação na época |
|---|---|---|---|---|---|
| porta | **0,80** | 0,27 | 0,002 | 0 | REJECT (alarme falso) |
| espelho | **0,81** | 0,20 | 0,007 | 0 | REJECT (alarme falso) |

**No olho:** **boas.**
- É a Luna, com cabelo longo escuro.
- Roupa, pose e cenário iguais. O celular e a mão foram mantidos no espelho.

**Alarmes falsos** (a imagem não foi estragada: a reconstrução de marcas foi descartada pela trava):
- **Porta:** a borda do braço contra a madeira marrom virou "tatuagem".
  - Correção: a região de marca precisa de miolo de tinta cinza (madeira: saturação ~0,50). Offline: 52 mil px → 0.
  - Na foto do quarto, 93% da tatuagem real continua detectada.
- **Espelho:** chinelos cinza e sombra da virilha viraram "tinta"; os chinelos também viraram "pulseira"; a caixa da mão + celular foi rotulada "brinco" e "relógio".
  - Correções:
    - marcas abaixo dos tornozelos são ignoradas;
    - joia só conta perto de pulso/cabeça;
    - caixa com rótulo de manter e de tirar: manter vence.
- **"Blocos"** eram traços do rosto/cabelo novos.
  - Correção: o bloco só é medido na pele reconstruída fora da identidade, com denominador mínimo.
  - Offline: porta 12 → 5,6; espelho 15,9 → 1,0 (passam).

**Limitação que fica:** a sombra cinza da virilha (entre as coxas, sobre o short) ainda pode virar "tinta". Nenhuma regra testada separou com segurança.


## Porta e espelho com Face Lock + corpo da Luna (2026-10-07, autorizado)

| | |
|---|---|
| Tempo e custo | 19,6 min, **~US$ 0,19** |
| Contexto | o usuário viu que as versões anteriores (0,80/0,81) "pareciam pessoas diferentes"; decisão: corpo da Luna vale mais que roupa idêntica |

**Porta: é a Luna.**
- Rosto igual ao da master (argolas e gargantilha dela), cabelo dela, corpo mais curvilíneo.
- Pose, porta, toalha e mãos intactas.
- Face Lock aceito (0,768 → 0,805).

**Espelho:**
- O Face Lock **era a Luna no olho**, mas foi descartado: o ArcFace deu 0,74 contra 0,79 do refino genérico. O número escolheu errado.
- Corpo, cabelo, celular, mão e chinelos: ok.

**Falhas e correções (commit dc61770):**

| Falha | Correção |
|---|---|
| Face Lock descartado pelo número | O Face Lock passa a ser a **autoridade** do rosto (sai só abaixo do piso 0,65 ou com defeito visível). Aprovação de identidade 0,72 |
| "Tatuagem" falsa no corpo redesenhado | As marcas antigas não contam onde o corpo foi redesenhado |
| "Blocos" que eram linhas novas da roupa | Linhas novas da roupa (barra, cordão) não contam |
| Sobra do cacheado original atrás do ombro | Faixa em volta do cabelo original também é refeita |

**Limitação aceita:** roupa redesenhada **parecida**, não idêntica. Na porta, o tomara-que-caia ganhou alças e o short ganhou cordão.

## Spec 46 na GPU: varanda + porta (2026-10-08, autorizado)

| | |
|---|---|
| Pod | `d54ru1tmvat0tl`, NVIDIA L4, US$ 0,49/h |
| Tempo e custo | 25,2 min, **~US$ 0,21** |
| Erro meu | a etapa da mão se chamava `hand_pose_lock`; o arquivo dela tinha `_pose_` e a sessão descarta essas imagens (prévias de pose), o que deu "terminou sem devolver imagem". Renomeada, com teste de guarda; ~5 min perdidos |

**Varanda: WARN, sem falhas.**

| Medida | Valor |
|---|---|
| Tatuagem | **0,0** |
| Rosto original | 0,18 |
| Pose | 0,018 |
| Fundo | 0 |
| Mãos (pontos de dedo) | 21/21 nas duas, gesto mantido |

- Corset, jeans, pulseiras, relógio e argolas mantidos.
- Defeitos:
  - um **morro pintado atrás da cabeça**: o texto da cena citava Copacabana e o modelo preencheu o lugar do coque loiro;
  - **óculos espelhados tratados como lente clara** (51% do brilho da pele contra limite de 45%), então a lente mudou;
  - Face Lock recusado (0,606 < 0,65): com óculos escuros o ArcFace cai;
  - mancha branca na borda do corset.

**Porta: não terminou dentro da trava (L4 mais lenta).** Parcial até o refino de rosto:
- **roupa idêntica com o corpo da Luna**, rosto e cabelo da Luna;
- defeitos: o corpo **inventou peças** na pele (alça no ombro, faixa de calcinha, marca na coxa).

**Correções (4dba889, sem GPU):**

| Correção | Detalhe |
|---|---|
| Óculos opacos | lente < 60% do brilho da pele ou sem pele real |
| Texto da cena só com a pessoa | roupa e pose; o cenário já está na foto |
| Piso do Face Lock com olhos cobertos por óculos mantidos | 0,5 |
| Negativos de peça de roupa inventada na etapa do corpo | folga maior da borda da roupa |

A mancha branca do corset deve cair com a folga, mas não foi verificada.

## A/B current × V2 em 3 fotos do usuário (2026-10-08)

Rodada no pod `z7m8uaspzaegc8` (RTX PRO 4000 Blackwell, US$ 0,57/h): 44 min, cerca de US$ 0,41, plano `plano_ab3.json` (sem retry).
Relatório em `generated/v2/engines/ab3/relatorio_ab3.html`. Todas as 6 execuções terminaram REJECT, e no olho também reprovam.

| Foto | Current (Qwen BFS) | V2 (sem Qwen) | Problema que os dois tiveram |
|---|---|---|---|
| Espelho com celular | 587 s; o Qwen saiu recusado (0,51 < 0,65), então o resultado ficou idêntico ao da V2 | 241 s | corpo, cabelo e rosto da Luna OK; halo claro na parede em volta do cabelo; faixa de pele clara na barra do short |
| Quarto com tatuagem | 565 s; Qwen aceito, com maquiagem pesada e argola nova | 247 s; rosto mais natural, rosto original 0,009 | mão com tatuagem virou mancha laranja; recortes retos no colo; resto de tatuagem no braço |
| Braço na cabeça | 500 s | 185 s | top branco virou preto; braço inventado; colar trocado; fundo inventado atrás do braço |

### Causas e correções (offline, 452 testes, não testadas na GPU)

1. **Máscara da mão media a altura do rosto.** Num close-up ela cobria antebraço e colo inteiros: o corpo da Luna pulava o braço e a tatuagem ficava.
   - Correção: a máscara agora segue os pontos da mão do DWPose ou o antebraço.
2. **Mão com o gesto travado, mas sem dedos medíveis.** Era o punho fechado segurando o top; a etapa foi aceita sem medida e produziu uma mão "fantasma". A limpeza de tatuagem em cima dela fez a mancha laranja.
   - Correção: a etapa só roda com gesto medível (≥ 10 pontos).
   - Correção: um detector de borrão (microtextura da zona ÷ pele em volta, mínimo 0,45) recusa a etapa da mão e a da tatuagem.
3. **Segmentador marcou "cabelo" em 78% da foto do braço.** A identidade cobriu 83% da imagem e o passe de rosto redesenhou tudo.
   - Correção: `hair.py` refaz a máscara pela cor do cabelo amostrada acima da testa. Na foto real ela cai de 4,4× para 1,6× a área do rosto.
4. **Roupa não segmentada com política PRESERVE.** Agora o corpo não é refeito, e a validação marca a roupa como "não conferida".
   - Correção: a medida da roupa passou a contar a roupa repintada por dentro da região. Antes ela saía vazia e o top preto passava sem aviso.
5. **Halo e faixa clara.** O fundo volta onde o modelo só repintou a parede. A faixa de pele entre o corpo novo e a roupa recebe o tom do corpo novo.

**Contradições entre medida e olho** (registradas como pede a spec):

- No quarto, o check de emenda deu 4,6–5,7 (PASS), mas os recortes retos no colo são visíveis.
- No braço, o fundo deu 0,0 alterado, mas há fundo inventado dentro da região da pessoa.

**Qwen:**

- Espelho: recusado pelo piso.
- Quarto e braço: aceito, mas trouxe maquiagem pesada e uma argola nova (joia inventada).

A V2 sem Qwen teve rosto mais natural e menos resíduo do rosto original no quarto, e levou de 2,3× a 2,7× menos tempo.

## V2 × V2.1: espelho e porta (2026-10-08)

Rodada no pod `9vy7m3y0f21axl` (RTX PRO 4000 Blackwell): 39 min, cerca de US$ 0,39 (teto autorizado US$ 0,48). Nenhuma execução foi reprovada pelo Gate; todas terminaram em WARN.

| Execução | Identidade | Rosto original | Tempo | No olho |
|---|---|---|---|---|
| espelho V2 | 0,78 | 0,24 | 482 s | rosto natural, pele uniforme, sem halo nem faixa; short levemente remodelado |
| espelho V2.1 | 0,69 | 0,21 | 555 s | pele contínua (pernas e braços no tom do rosto), mas o Qwen trouxe argola e maquiagem pesada, e apareceu um debrum branco na barra do short |
| porta V2 | 0,78 | 0,18 | 403 s | rosto e cabelo da Luna; mesma roupa; **alça inventada** no top tomara-que-caia |
| porta V2.1 | 0,77 | 0,32 | 592 s | argolas (Qwen), gargantilha (provavelmente da LoRA: é um traço SOFT da Luna), maquiagem pesada; a mesma alça inventada |

Conclusões:

- **Qwen no rosto:** mesmo só no rosto e com contexto de preservação, ele copia a maquiagem e as argolas da master. As orelhas ficam dentro do `face_full`. Recomendação: desligar o Qwen por padrão.
- **Corpo:** com a roupa PRESERVE (pixel a pixel), busto, cintura e quadril continuam com o formato da roupa da pessoa original. Só braços, ombros, pernas e pele mudam. Para o corpo da Luna aparecer, a roupa precisa ser redesenhada (clothing RECONSTRUCT).
- **Alça inventada:** acontece nas duas versões. O negativo `body_skin_negative` não basta; falta um detector de peça nova na pele.
- **Bug corrigido depois da rodada:** a identidade de pele saiu "master sem pele" porque o campo da master é `content` e eu tinha usado `data`.

## V3 na GPU: espelho e porta (2026-10-09)

Antes desta rodada, a V3 foi tentada 7 vezes (08/10 à noite e madrugada de 09/10), cerca de US$ 0,99 no total, sem resultado. O ComfyUI travava esperando o disco de rede do EU-RO-1 (`request_wait_answer`). O que destravou:

- **Sondagem antes do teste:** liga o pod sem teste e só sobe a V3 se o ComfyUI continuar respondendo depois da inicialização do Manager.
- **Script do pod:** passou a ter tempo limite em todos os `curl` (7ccb162).

Esta rodada (pod `lpfimwvl6lk3vp`, RTX PRO 4000 Blackwell) custou ~US$ 0,24, com sondagem e teste juntos.

| Foto | Tempo | Identidade | Halo (dE) | Gate | No olho |
|---|---|---|---|---|---|
| espelho V3 | 486 s | 0,81 (V2: 0,78) | 2,85 (PASS) | REJECT por roupa | **uma pessoa só, coerente**: corpo da Luna (atlético e curvilíneo), pele e luz iguais do rosto aos pés, sem halo. Problemas: o top virou regata comprida com decote (sem o laço), o short virou short de corrida com cordão e debrum branco, e o **celular mudou de branco para preto** |
| porta V3 | 422 s | 0,74 (V2: 0,78) | 1,40 (PASS) | REJECT por roupa | **o melhor resultado até agora**: corpo da Luna, top tomara-que-caia **sem a alça inventada** que a V2 punha, rosto natural, sem halo. Problemas: o top perdeu a textura canelada e o decote coração, e o short perdeu o botão e os passantes (virou short com cordão) |

O que aprendemos:

- A reconstrução inteira resolve o que a V2 não resolvia: corpo da Luna, uma pele só, sem halo, sem alça inventada.
- **A roupa perde detalhes.** A ClothingCondition só tinha cor, alças e corte: a legenda de roupa do Florence veio vazia (`fields.clothing`). Botão, passantes, textura canelada, decote e laço não entram no texto.
- **Objetos seguros (o celular) não estão protegidos.** O celular não vira camada de acessório.
- **Falso positivo no ClothingValidator:** o corpo da Luna é maior, então a roupa cobre áreas que eram pele na foto. Isso contou como "peça nova sobre a pele" (10,6%). A "alça inventada" na porta também foi falso positivo: provavelmente o cabelo escuro sobre o ombro.

### V3, 2ª rodada (2026-10-09, com as correções 30e00d9)

Pod `lpfimwvl6lk3vp` (RTX PRO 4000 Blackwell). Sondagem + teste: ~US$ 0,25. Antes houve uma falha de ~1 min no script, por causa de `Cache` sem kwargs.

| Foto | Identidade | No olho |
|---|---|---|
| espelho | 0,67 (1ª: 0,81) | **pior**: short virou branco com cordão, top virou regata comprida; celular continuou preto |
| porta | 0,66 (1ª: 0,74) | short voltou a ter botão e passantes, mas o top virou **verde-oliva com botões na frente** |

Por quê:

- **A legenda detalhada do Florence por peça INVENTA detalhes.** No espelho escreveu "drawstring waistband" (o short não tem cordão). Na porta escreveu "button-down front" e "cinched with a thin strap", e esses botões foram parar no top. O texto longo ainda diluiu a identidade. **Desligado** (`garment_details: false`).
- **Celular:** a camada só cobriu 1.455 de ~15.000 pixels da caixa, porque o celular bege foi lido como pele. Agora objeto seguro = **caixa inteira**, e só perto de um pulso do DWPose. Foi um travesseiro que virou "bolsa".
- **Conclusão:** texto não segura detalhe de roupa. Para manter a roupa idêntica com o corpo da Luna, falta condicionamento **visual** da roupa: pixels da roupa original onde a forma coincide + passe leve de integração, ou um adaptador de imagem para roupa.

### V3, 3ª rodada: braço na cabeça e rua bege (2026-10-09)

Fotos enviadas pelo usuário. Pod `lpfimwvl6lk3vp`, sondagem + teste ~US$ 0,24. O Gate não reprovou o espelho (WARN) e reprovou a rua por borda.

| Foto | Identidade | No olho |
|---|---|---|
| espelho, braço na cabeça | 0,68 | **muito melhor que a V2 de 08/10** (lá o top branco tinha virado preto e o braço tinha sido inventado): top branco de renda mantido, rosto e cabelo da Luna, piercing do nariz removido. Defeitos: manchas marrons no braço levantado, **pulseiras douradas inventadas** no outro braço, colar trocado por coração, **busto aumentado** |
| rua, conjunto bege | 0,75 | **o melhor da V3 até agora**: rosto e corpo da Luna, top tomara-que-caia e calça pantalona mantidos, relógio e anéis no lugar. Defeito: **manchas mais claras na calça** |

Causas, corrigidas em 08ca8be (offline):

- **Texto da roupa** (frases da legenda do Florence): entraram junto "accentuates her large breasts and cleavage" (corpo da pessoa ORIGINAL), "gold heart-shaped necklace" e "gold bracelet on her left wrist" (joias descritas errado, redesenhadas no braço errado) e o cenário. Agora só entram as orações de roupa: "a white, lace-trimmed top", "a strapless beige crop top and wide-legged pants".
- **Manchas na calça:** com a roupa não segmentada, a calça cor de pele virou "perna", e a continuidade de pele mudou o tom dela em manchas. A continuidade agora desliga quando a roupa não foi segmentada.

## V3.1: diagnóstico de máscaras (2026-10-09)

Antes de gastar GPU com reconstrução, rodei **só as máscaras** com `mascaras_v31.py`. O script usa o mesmo `_scene` e a mesma config da V3.1.

**Custo da rodada: ~US$ 0,25 (teto US$ 0,30), sem reconstrução.**

- Primeiro pod: ~13 min. O pod se desligou sozinho por inatividade do estúdio: a sondagem e as máscaras rodaram sem o keepalive que o `rodar_pod.sh` manda. Erro meu.
- Segundo pod: ~12,5 min, desta vez com keepalive.

Arquivos em `generated/v2/engines/v31/mascaras/saida_mascaras/`: painel por foto (`*__mascaras.jpg`), imagem por classe e `mascaras.json`.

| Critério | Rua (bege) | Espelho (top branco) |
|---|---|---|
| Roupa reconhecida como roupa | ✅ 70% da pessoa (top + calça bege) | ✅ 20% (top de renda) |
| Pele exclui roupa, cabelo, acessórios e fundo | ✅ 0 px de vazamento | ✅ 0 px |
| Ambíguas fora da correção de pele | ✅ tecido cor de pele = 99% da roupa, excluído | ✅ 52% da roupa, excluído |
| Braço levantado separado de cabelo/fundo | — | ✅ pessoa e pele; ❌ a máscara de mãos/braços (DWPose) marca o tronco e não o braço levantado |
| Acessórios reais | ✅ colar, relógio, anéis, brincos | ✅ relógio, colar, anel |
| Acessórios inexistentes | ❌ "celular" sobre as mãos: colaria as mãos originais | ❌ "óculos de sol" nos olhos: colaria olhos e sobrancelhas originais no rosto da Persona |

**Veredito: REPROVADO.** A roupa passou, mas o grounding do Florence devolve uma caixa para cada palavra pedida, mesmo sem o objeto. Pelas regras combinadas, a reconstrução **não** foi executada.

**Correções** (offline, 489 testes):

- **Exigência de evidência** (`require_caption_for: glasses, object`): óculos e objetos seguros só valem se a legenda da foto os mencionar. Para celular, também valem "selfie" e "mirror". Um "objeto" que é mais de 35% pele (mãos) é recusado.
  - Conferido nas caixas reais desta rodada: óculos do espelho → IGNORE; celular da rua → IGNORE; colar, relógio, anéis, brincos e pulseiras → continuam PRESERVE.
- **Preservação da roupa:** só protege a área de mão quando a mão tem dedos detectados. O pulso estimado caiu no tronco e cobriria o top.

**Pendências:**

- A máscara de braços do DWPose no espelho continua imprecisa. Ela só afeta a divisão por região da continuidade de pele, que agora roda apenas na pele validada pela segmentação. O próximo diagnóstico salva os keypoints para confirmar.
- Um pano vinho no canto do espelho conta como pessoa e roupa (efeito pequeno: seria preservado como estava).

## V3.1 em producao - 1a troca real (09/10, pelo site, pod wvel1d18h8gb1k L4 US$ 0,59/h)

Rua bege, QUALITY, max_retries 0, codigo main 13daa37. Download dos 4 modelos do HF oficial: 113 s. Job: 547 s
(GPU 126 s). Custo do pod do teste: ~US$ 0,16 (saldo 3,359 -> 3,202). Status **REJECT** (seams 18,6).
Medidas: identidade 0,708 (WARN), clothing_v3 PASS, boundary PASS (sem halo), pose/cenario/composicao PASS,
skin WARN (textura exagerada), photometric WARN (grao 0,48x), skin_continuity WARN (ombros->bracos).

No olho:
- Melhor que a V3 na mesma foto: calca e top na cor certa, sem as manchas de tom da V3, fundo intacto, sem halo, pose e relogio mantidos.
- Rosto da Luna natural (sorriso aberto virou sorriso fechado).
- **Defeitos:** contorno escuro (oliva) em volta do top tomara-que-caia (borda da colagem da roupa); mancha translucida bege na cintura onde as maos originais cobriam a calca; graos/pontilhado na pele e na calca (grain_match forte demais); aneis e colar com pingente da original sumiram.
- Imagens: generated/v2/engines/v31_prod/.

### Reteste apos 14e2702 (09/10, pod engbyacutsg3j5 RTX PRO 4000, seed 7801, keep_intermediates)

REJECT (seams 17,4). Job 488 s. Custo do reteste ~US$ 0,23 (o pod ficou 8 min ocioso depois do job ate o desligamento
automatico; o acompanhamento por SSH travou) + ~US$ 0,03 para religar e buscar as imagens no volume.
Etapas em generated/v2/engines/v31_prod/reteste/ (+ telemetria.json).
- Resolvido: contorno oliva do top sumiu; pontilhado sumiu (grao so na pele gerada: 3,69 -> 4,32, teto 2,5); colar com pingente voltou.
- Novo/restante: o top original alinhado (escala y 0,90, deslocamento y +27 px) fica com cara de adesivo (borda dura, reta em cima);
  mancha na cintura continua (agora com borda dura); faixa clara do lado direito da calca (fantasma da pessoa original) e
  contorno claro no ombro direito.
- Conclusao: colar a roupa original num corpo diferente gera bordas duras; proximo passo proposto = clothing_preservation
  desligado (roupa da reconstrucao, como na V3, que ficou limpa no top) mantendo grao/negativos/continuidade validada.

### Trocas do usuario pelo site (09/10, pod hurv7p7e3pczlf) e correcoes

- **Quarto (lingerie vermelha):** REJECT (seams 14,8; clothing_v3 cor dE 19,7 e alca inventada 16%). Roupa redesenhada:
  `pants`/`shirt` do Florence marcaram a CAMA, a uniao ficou 34% dentro da pessoa e a roupa inteira foi rejeitada (sem
  preservacao). Tatuagem inventada na mao esquerda. Contorno claro no braco/lateral (fantasma da pessoa original).
- **Espelho (calcinha preta):** REJECT (seams 28,5). Roupa da foto original nao aceita (clothes_orig null). Tatuagem do
  antebraco da pessoa original continuou; contorno claro no braco/cintura; colar e brinco trocados.
- Correcoes (sem GPU): workflow `replacement-segment-v31` (+lingerie, bikini, underwear; cada pedido fora da pessoa e
  descartado sozinho, min_frac 0,02); passe `clothing_harmonize` (peca inteira + 6 px, denoise 0,42, guarda de cor 8 dE)
  no lugar da emenda fina; `ghost_color_fix` (campo de cor do fundo real no fantasma); `invented_markings` (tinta na pele
  da Persona -> entrada sem o traco + passe LoRA 0,55). Nao testado na GPU.

### Teste de 267ea86 nas 3 fotos (09/10, pod 6k4qp7qi94oobk, ~US$ 0,29) - generated/v2/engines/v31_prod/teste3/

Todas REJECT. Rua: top reconstruido (escala 1,13x1,47 vs original: os pedidos novos pegaram pele) e saiu marrom; calca boa,
sem pontilhado; mancha menor na cintura. Quarto: nada colado (top 1,29x, calcinha cobertura 0) -> lingerie redesenhada;
tatuagem da mao saiu; face_lock Qwen adaptativo (identidade 0,63) trouxe maquiagem pesada; fios loiros sobraram.
Espelho: roupa 'full' escala 0,49 -> nada colado; **invented_ink marcou 36438 px de pele sa e o passe LoRA 0,55 redesenhou o
peito (regressao)**; tatuagem do antebraco da original continuou. Rollback em 72cf2a7: invented_markings desligado,
workflow de segmentacao original. Diagnostico: a preservacao por pixels so funciona quando o corpo novo tem quase o mesmo
contorno da roupa original - nas 3 fotos o alinhamento por caixa falhou.
