# Troca de pessoa pela Luna: auditoria, pesquisa e proposta de arquitetura

09/10/2026. Nada foi implementado, nenhuma GPU foi ligada e nenhuma imagem foi gerada para este relatório.

**Legenda de evidência** (aparece em cada afirmação importante):
- **[TESTE]** comprovado nos nossos testes, com arquivo e data.
- **[DOC]** confirmado em documentação ou repositório externo.
- **[HIPÓTESE]** precisa ser validado antes de virar decisão.

---

## 0. Resumo executivo

**A troca de pessoa inteira está no caminho errado para o objetivo que você definiu.** O objetivo é preservar o máximo da foto (roupa, lingerie, pose, mãos, fundo) e trocar a identidade. A V3/V3.1 faz o contrário: redesenha a pessoa inteira com denoise 0,85 e depois tenta *devolver* o que foi destruído (roupa colada, cor do fundo, pele contínua, tatuagem inventada). Cada correção conserta um sintoma e cria outro. Isso foi visto em 9 rodadas pagas, entre 08 e 09/10, cerca de US$ 3.

**A recomendação é uma arquitetura híbrida, com o "modo cabeça" como padrão:**
1. Trocar só cabeça e cabelo com o que **já está em produção** no Pack, na História e no Trocar vídeo: Qwen-Image-Edit 2511 + LoRA BFS Head V5. Foi o vencedor do nosso benchmark de 07/10 (semelhança 0,727, 22 s).
2. Compor a cabeça nova na foto com máscara orgânica, correção de cor no anel e borda suave (`compor_cabeca.py`, já escrito e testado no benchmark).
3. Tudo fora da cabeça fica **idêntico pixel a pixel**: roupa, lingerie, mãos, corpo e fundo. Por construção, não por correção.
4. Remoção de tatuagem só quando pedida, como etapa local, separada e opcional.

**Por que isso resolve a maior parte dos problemas:** a maioria dos defeitos (lingerie redesenhada, roupa trocada, braço deformado, pele com tom errado, halo, acessório inventado no corpo) nasce de regenerar regiões que não precisavam ser regeneradas. No modo cabeça, essas regiões não entram no modelo.

**O que você perde:** o corpo continua sendo o da pessoa da foto, não o da Luna. Isso precisa ser decidido por você (seção 4.3). Já foi um ponto de atrito em 07/10, quando as trocas só de rosto "pareciam pessoas diferentes".

**Custo esperado:** cerca de US$ 0,02–0,03 por imagem no modo cabeça, contra cerca de US$ 0,08–0,09 da V3.1. É uma estimativa a partir de tempos medidos (seção 8). E sem baixar 14 GB de modelos por sessão, porque o Qwen já está no volume.

**Antes de gastar qualquer crédito:** uma fase 0 inteiramente local, sem custo. Ela usa as cabeças que já foram geradas no benchmark e no teste do quarto (07/10) para calibrar a composição e as métricas. Depois, uma prova de conceito com teto de US$ 0,35.

---

## 1. Auditoria do que existe

### 1.1 Pipelines de troca no projeto

| Pipeline | Onde | O que faz | Estado | Evidência |
|---|---|---|---|---|
| **Troca de cabeça Qwen 2511 + BFS V5** | `backend/app/services/head_swap.py`, `workflows/qwen-bfs-head-swap.json` | Recorte em volta do rosto (3,6× o rosto), Qwen refaz cabeça e cabelo com a luz da cena e cola de volta o **recorte inteiro** com borda suave (FeatherMask retangular) | **Em produção** (História, Trocar vídeo, Pack) | [TESTE] benchmark 07/10: 0,727 de semelhança média, 22 s/img na PRO 4500. Quarto: 0,750, "nada mudou fora da cabeça" (`docs/BENCHMARK_TROCA_CABECA.md`) |
| `compor_cabeca.py` | `scripts/bench_cabeca/` | Composição **por máscara orgânica**: alinhamento ECC, máscara = cabeça original ∪ o que mudou perto da cabeça, menos outras pessoas, correção de cor de baixa frequência no anel, grão, degradê suave | **Só no benchmark**, não em produção | [TESTE] usado para medir as 42 execuções de 07/10 |
| Pack antigo (`person_swap.py`) | backend | Qwen + InsightFace + InstantID, cabeça escondida em cinza | Produção antiga | [TESTE] 20/20 pessoa certa, ~5,5–7 min/foto (02/10) |
| **Replacement V2/V2.1** | `core/engines/replacement.py` | SDXL RealVisXL + LoRA lunavox, passes por região (rosto/cabelo, corpo, tatuagem), roupa PRESERVE | Branch | [TESTE] porta/espelho 07/10: identidade 0,80/0,81, "boas no olho", roupa e cenário iguais |
| **Replacement V3/V3.1** | `replacement_v3.py`, `v31_integration.py` | Reconstrução da pessoa inteira (denoise 0,85) + dezenas de correções | **Em produção desde 09/10** (aba Trocar pessoa) | [TESTE] todas as rodadas de 09/10: REJECT; ver 2.1 |
| Retoque de tatuagem | `scripts/retoque/retoque_tatuagens.py` | Máscara orgânica de tinta, pré-preenchimento, composição com degradê, checagem | Script local + pod | [TESTE] teste 7: ombro e argola **perfeitos**; mão e antebraço viraram "massa/pelo" |

### 1.2 Modelos

| Modelo | Onde está | Licença | Uso |
|---|---|---|---|
| Qwen-Image-Edit 2511 fp8 + Lightning 8 passos | volume EU-RO-1 | Apache 2.0 [DOC] | troca de cabeça |
| BFS Head V5 (rank 16 merged) | volume | MIT [DOC] | troca de cabeça (ordem de entrada invertida) [DOC] |
| `luna_qwen_2511_v1` (LoRA da Luna para Qwen) | volume | própria | [TESTE] não melhorou a troca (0,721 contra 0,727) |
| RealVisXL V5 fp16, ControlNet Union promax, InstantID | **fora do volume**: 14 GB baixados do HF a cada sessão (~2 min) | OpenRAIL++ / Apache | V2/V3 |
| LoRA `lunavox_sdxl_v1` | volume | própria | V2/V3 |
| Florence-2 large, SAM2, DWPose, Depth Anything V2 | volume | MIT / Apache [DOC] | segmentação, pose |
| InsightFace antelopev2 (LunaFaces, InstantID e a métrica de identidade) | volume | **pesos só para pesquisa não comercial** [DOC: model zoo do InsightFace]; o código é MIT | escolha do rosto, InstantID, ArcFace |
| Qwen-Image-2.1 + BFS 2.1 | não instalado | **licença de pesquisa** | [TESTE] 0,800 no benchmark, melhor de todos, mas não pode ir para produção sem licença |

### 1.3 Infraestrutura

| Problema | Impacto | Evidência |
|---|---|---|
| Disco de rede EU-RO-1 trava (MooseFS, `request_wait_answer`) | ~US$ 0,99 perdidos em 08/10 | [TESTE] |
| A cada ligação o wake pode criar um **pod novo**; os parados cobram disco | o saldo cai com tudo desligado (2,36 → 2,30 em 09/10) | [TESTE] |
| Desligamento por ociosidade (8 min) | resultado de 09/10 não baixado; pod desligou antes | [TESTE] |
| RealVisXL/ControlNet/InstantID baixados por sessão | +2 min e ~14 GB a cada ligação | [TESTE] 113 s |
| PC local: Intel UHD, 7,7 GB RAM, sem NVIDIA | dá para testar segmentação leve e composição na CPU; modelos grandes, não | [TESTE] `INVENTARIO_SISTEMA_LUNA.md` |

### 1.4 Frontend e API (estado atual)
- Aba **Trocar pessoa**: foto → Gerar → resultado; V3.1 fixa em `runSimpleReplacement` (`frontend/src/api/client.ts`).
- Download corrigido em a61fe96: a imagem é carregada no navegador assim que fica pronta.
- `/api/v2/replace` com token; jobs em memória (perdidos se o backend reiniciar); imagem servida pelo ComfyUI do pod (some quando o pod desliga, salvo se o navegador já baixou).
- A troca de cabeça BFS **não tem rota própria** para "foto → Luna". Ela só é usada dentro da História, do Pack e do Trocar vídeo.

---

## 2. Diagnóstico: por que cada falha aconteceu

### 2.1 A causa-mãe

**A V3 regenera a pessoa inteira e depois tenta devolver o que não deveria ter mudado.**

Num inpaint SDXL com denoise 0,85, tudo dentro da máscara vira uma amostra nova do modelo. O modelo é guiado por:
- **texto:** "roupa bege", que não carrega o modelo exato da peça;
- **pose e profundidade:** carregam a forma, não o tecido;
- **LoRA:** carrega os vícios do dataset da Luna (colares, argolas, maquiagem).

Detalhes da roupa, renda, tela cor de pele, tatuagem ou ausência de tatuagem, tom de pele por região e contorno exato **não estão em nenhum desses sinais**. Então o modelo inventa.

Tudo que foi construído depois, na V3.1, é reparo de saída:
- colar a roupa original de volta;
- corrigir a cor do fundo no "fantasma";
- igualar a pele;
- caçar tatuagem inventada;
- travar a roupa com Differential Diffusion.

Cada reparo depende de uma segmentação perfeita da imagem gerada **e** da original, e a segmentação é justamente o ponto mais fraco (2.2).

### 2.2 Falha por falha

| Falha observada | Por que aconteceu | Evidência | Como a nova arquitetura evita |
|---|---|---|---|
| **Lingerie redesenhada** (corações → sutiã comum, bloco bege) | Reconstrução a 0,85 + roupa descrita por texto. A tela cor de pele não entrou na máscara de roupa: o detector de roupa por cor a vê como pele, e o Florence `pants`/`shirt` marcou a **cama** (união 34% dentro da pessoa) | [TESTE] teste3/teste4 09/10; `v31_segmentation` | No modo cabeça a lingerie **não passa pelo modelo**. Zero dependência de segmentar roupa |
| **Roupa com cara de adesivo** (V3.1) | Colagem da roupa original alinhada por **caixa** num corpo de outro tamanho (escala 0,90 / deslocamento +27 px; outra foto 1,13×1,47) | [TESTE] reteste 09/10 | Sem colagem: a roupa nunca saiu do lugar |
| **Tatuagem na Luna** (antebraço do espelho) | A tatuagem da pessoa original só era procurada **se a roupa fosse segmentada**. A reconstrução a 0,85 **recria** a tatuagem a partir da imagem de entrada | [TESTE] espelho 09/10 | Tatuagem vira etapa **opcional e local**, com máscara revisável. Sem tatuagem pedida, a pele fica intacta |
| **Tatuagem inventada** (mão, quarto) | Prior da LoRA / do SDXL ao redesenhar mãos | [TESTE] quarto 09/10 | Mãos fora do modelo (a máscara da cabeça exclui as mãos pelos pontos do DWPose) |
| **Detector de tinta marcou 36 mil px de pele sã** e o passe redesenhou o peito | Regra de cor (mais escuro e mais frio que a vizinhança) em pele bronzeada com sombra; nenhum limite de área | [TESTE] teste3 09/10 | Detecção de tatuagem só quando pedida, com confirmação por detector semântico **e** limite de área **e** pré-visualização da máscara antes de gastar |
| **Halo / contorno claro** em volta do corpo | O lugar onde a pessoa original estava (o "fantasma") só pode ser preenchido por fundo **inventado**, que sai com outra luz | [TESTE] todas as rodadas V3 | Não existe fantasma: o corpo não muda de contorno |
| **Pele alaranjada / tom errado no corpo** | Regenerar pele inteira (V1.4 de 06/10) ou a continuidade de pele global | [TESTE] V1.4, V2.1, espelho 09/10 | O corpo não é regenerado. Só a junção do pescoço recebe correção de cor de baixa frequência |
| **Braço/mão deformados ("massinha")** | Inpaint com preenchimento liso + profundidade calculada sobre a tinta + denoise 0,55 | [TESTE] testes 2–7 de 06/10 e 07/10 | Mãos não passam pelo modelo no modo cabeça. Tatuagem em mão vira caso de risco declarado (seção 9) |
| **Maquiagem pesada e argolas** | Viés da **referência** (a master principal usa argola grande e maquiagem) e o Face Lock Qwen copiando a master | [TESTE] benchmark 07/10 ("a referência principal usa argola grande"), V2.1 08/10, quarto 09/10 | Curadoria de **uma referência de cabeça limpa** (sem maquiagem forte, brinco pequeno ou nenhum) e checagem de acessório novo na região da cabeça |
| **Rosto "colado" na cabeça** | Colagem do Qwen em retângulo com borda de 4 px e tom diferente | [TESTE] quarto 09/10 | Máscara orgânica de cabeça e cabelo + correção de cor no anel + degradê proporcional (`compor_cabeca`) |
| **Fios loiros da pessoa original sobrando** | Cabelo longo além do recorte do Qwen, ou fora da máscara | [TESTE] quarto 09/10 | Recorte do Qwen dimensionado pela **máscara de cabelo original**, não só pelo rosto; checagem de resto de cabelo (seção 7) [HIPÓTESE] |
| **Integração global estragou rosto aprovado** | Passes globais (degrau pele × pele, integração fotométrica) depois do rosto | [TESTE] smoke 07/10, V2 07/10 | Nenhum passe global depois da cabeça. A composição é a última etapa e só age dentro da máscara |
| **Métrica contra o olho** | Emenda medida na faixa externa (cabelo novo cobre fundo = "emenda"); ArcFace escolheu o rosto errado | [TESTE] benchmark 07/10, espelho 07/10 | Métricas recalibradas com imagens já julgadas por você (seção 7), antes de qualquer rodada |
| **Pod travado, resultado perdido, pods duplicados** | Disco de rede, ociosidade, failover | [TESTE] | Sondagem antes, keepalive durante o lote e download obrigatório antes de desligar; lista de pods para você apagar |

### 2.3 O que os testes provaram que funciona

- **Rosto e cabelo da Luna naturais e reconhecíveis:**
  - [TESTE] V2 porta/espelho de 07/10: 0,80/0,81, "boas no olho";
  - [TESTE] benchmark BFS: 0,65–0,86 por imagem.
- **Fundo intacto quando nada fora da cabeça é regenerado:**
  - [TESTE] quarto com BFS + `compor_cabeca`: "nada mudou fora da cabeça".
- **Tatuagem em pele aberta (ombro) e joias perto do rosto (argola):** saem limpas com máscara orgânica + pré-preenchimento + degradê.
  - [TESTE] teste 7.
- **Roupa idêntica quando a roupa não é regenerada:**
  - [TESTE] V2 de 07/10;
  - V3.1 com trava de roupa na rua: clothing_v3 PASS (09/10).

---

## 3. Arquiteturas possíveis

| | A. Pessoa inteira (V3/V3.1, atual) | B. **Cabeça + preservação (híbrida)** | C. Cabeça + corpo da Luna por região (V2) | D. Modelo de edição por instrução, foto inteira (Qwen/Kontext "troque a pessoa") |
|---|---|---|---|---|
| Identidade da Luna | boa (0,68–0,81) [TESTE] | boa (0,65–0,86; média 0,727) [TESTE] | boa (0,78–0,81) [TESTE] | boa, mas troca roupa e cenário (10/10 no benchmark de 05/10) [TESTE] |
| Roupa/lingerie preservada | **não** [TESTE] | **sim, por construção** | sim, se a segmentação acertar; senão vira pele [TESTE] | **não** [TESTE] |
| Corpo da Luna | **sim** | **não** (corpo da foto) | parcial (braços, pernas; busto e quadril seguem a roupa) [TESTE] | sim, mas com a roupa trocada |
| Mãos/braços | risco alto [TESTE] | intactos | risco médio [TESTE] | risco médio |
| Halo/emendas | frequentes [TESTE] | só na borda da cabeça (tratável) | frequentes [TESTE] | raras, mas a imagem inteira muda (deriva de pixels) [DOC] |
| Tatuagem da original | precisa remover antes [TESTE] | fica, salvo se pedida a remoção | precisa remover [TESTE] | some ou muda sem controle |
| Custo/imagem | ~US$ 0,08–0,09 [TESTE] | **~US$ 0,02–0,03** (estimativa) | ~US$ 0,04–0,08 [TESTE] | ~US$ 0,02 |
| Modelos por sessão | 14 GB baixados | nenhum (Qwen no volume) | 14 GB | nenhum |
| Complexidade | muito alta (≈ 4.900 linhas nos módulos da engine) | baixa/média (reaproveita produção + benchmark) | alta | baixa, mas sem controle |

**Recomendação: B como padrão, A/C como modo opcional "corpo da Luna" só para roupa simples.** D foi descartado pela evidência de 05/10: troca roupa e cenário.

---

## 4. Tecnologias e decisões

### 4.1 Troca de cabeça: Qwen-Image-Edit 2511 fp8 + BFS Head V5 (manter)
- **Por quê:**
  - vencedor do nosso benchmark entre as opções liberadas para produção [TESTE];
  - já está no volume e em produção;
  - Apache 2.0 + MIT [DOC];
  - o autor recomenda a V5 para a 2511, com ordem de entrada invertida [DOC].
- **Alternativas consideradas:**

| Alternativa | Por que não agora |
|---|---|
| Qwen-Image-2.1 + BFS 2.1 | Melhor no benchmark (0,800) [TESTE], mas tem **licença de pesquisa**. Fica como opção se você conseguir a licença comercial. A BFS 2.1 saiu em 25/09/2026 [DOC] |
| InfiniteYou / PuLID / InstantID | São geradores condicionados por identidade: bons para criar a pessoa numa cena nova, **não ancoram expressão e luz da foto-alvo** [DOC: comparação do InfiniteYou contra PuLID]. Não há benchmark público de troca em foto-alvo [DOC] |
| ACE++ (FLUX Fill) | Tem modo de troca de rosto, mas a evidência é só de comunidade, sem métrica [DOC]. FLUX tem licença não comercial (dev) |
| inswapper/ReActor | 128 px, rosto sem cabelo. Não troca o cabelo, que é regra da Luna |

- **Riscos conhecidos do BFS:**
  - rejuvenesce (~22 anos) [TESTE];
  - copia maquiagem e argola da referência [TESTE];
  - pequena deriva de pixels no recorte inteiro [DOC: o redimensionamento interno da 2511 desloca a saída; a correção é recortar, editar e recompor por máscara, ou alinhar com ECC/AKAZE].

### 4.2 Composição (o ponto que mais muda o resultado visual)

O que existe hoje em produção (`head_swap.py`):
- cola o **recorte inteiro** com borda retangular;
- isso traz de volta a deriva do Qwen no recorte todo (ombros, colo, roupa perto do pescoço).

Proposta: produtizar o `compor_cabeca.py` (já validado no benchmark):
1. **Alinhamento** ECC da saída com a original, na região da cabeça (já existe). Rejeitar se a correlação for baixa.
2. **Máscara M** = cabeça e cabelo **originais** ∪ cabeça e cabelo **novos**, dentro de uma zona limitada em volta do rosto. Excluir:
   - mãos (pontos do DWPose);
   - outras pessoas;
   - acessórios mantidos.

   Dois avanços sobre o atual:
   - **cabelo original pela segmentação**, e não só pela diferença de cor, para pegar o fio loiro que não mudou;
   - **cabelo novo pela matte do BiRefNet**, para fios finos [DOC: BiRefNet nativo no ComfyUI, variantes HR/matting para cabelo] [HIPÓTESE quanto ao ganho].
3. **Cor:**
   - campo de cor de baixa frequência (original − novo) medido no **anel fora de M**, aplicado dentro de M (já existe);
   - o pescoço recebe o tom do colo da foto, sumindo com a distância.
4. **Borda:** degradê proporcional pela distância (já existe). Comparar com mistura Laplaciana em várias bandas e com Poisson na borda do pescoço [DOC: Laplaciana dá transição larga e suave mas não corrige diferença grande de cor; Poisson corrige a cor mas pode desbotar ou puxar o tom]. Decidir **na fase 0, sem custo**, com as cabeças já geradas.
5. **Fora de M:** a original, byte a byte. É verificado automaticamente (seção 7).

### 4.3 Decisão que é sua: corpo da Luna × foto preservada

Não há como ter as duas coisas ao mesmo tempo com a tecnologia testada:
- **Corpo da Luna** exige regenerar o corpo, e então a roupa vira texto e se perde [TESTE: V2.1 de 08/10, "com a roupa pixel-idêntica, busto, cintura e quadril ficam os da pessoa original"].
- Em 07/10 você decidiu que o corpo da Luna valia mais que a roupa idêntica. Agora o pedido é manter a lingerie e "o mesmo padrão do corpo".

Proposta:

| Modo | O que faz | Para quê |
|---|---|---|
| **"Luna na foto"** (padrão) | cabeça e cabelo da Luna, resto da foto intacto | lingerie, roupa complexa, nu parcial, mãos em cena |
| **"Luna inteira"** (opcional, experimental) | V3 sem os reparos da V3.1, avisando na tela que a roupa será redesenhada parecida | roupa simples |

### 4.4 Segmentação (mais simples no modo cabeça)

| Necessidade | Hoje | Proposta | Evidência |
|---|---|---|---|
| Cabelo | Florence "hair" + correção por cor (`hair.py`) | manter + matte BiRefNet na borda | [TESTE] Florence chegou a marcar 78% da foto como cabelo (corrigido); [DOC] BiRefNet para cabelo |
| Mãos | DWPose (pontos) | manter para excluir as mãos de M | [TESTE] |
| Roupa | Florence com vários pedidos + cor | **não é necessária no modo cabeça.** Para o modo opcional: avaliar SAM 3 (texto "lingerie", "bra") [DOC: segmentação por conceito; licença SAM permite uso comercial com condições] e SegFormer-B2-clothes (18 classes, **sem classe de sutiã/roupa íntima** [DOC]) | |
| Tatuagem | regra de cor (falsos positivos) | SAM 3 "tattoo" + confirmação por cor + limite de área + **máscara mostrada antes de gastar** [HIPÓTESE] | [TESTE] falso positivo de 36 mil px |
| Corpo inteiro | SAM2 por pontos | manter | [TESTE] |

**Descartado:** Sapiens (Meta), apesar de ter 28 classes de partes do corpo, porque os pesos são **CC-BY-NC 4.0, não comerciais** [DOC].

### 4.5 Tatuagem (só quando pedida)
- **O que já funcionou:** pele aberta e joias perto do rosto [TESTE teste 7].
- **O que falha:** mãos e dedos (vira massa) [TESTE testes 2–7].
- **Candidatos para a prova de conceito**, nos mesmos recortes:

| Candidato | Como funciona | Observação |
|---|---|---|
| (a) | fórmula atual do retoque: SDXL + LoRA, denoise ~0,45, pré-preenchimento | baseline |
| (b) | **LaMa** para tirar o traço + passe leve (Differential Diffusion ≤ 0,35) só para textura | o LaMa roda na CPU e dá para testar sem custo; [DOC: LaMa vai bem em textura uniforme e pior em textura complexa] |
| (c) | **Qwen-Image-Edit** por instrução ("remove the tattoo") no recorte, composto só na máscara | o Qwen já está no volume [HIPÓTESE] |

- Regra: mão com tatuagem é caso de risco. O sistema avisa e mostra antes de entregar.

---

## 5. Pipeline completo proposto (modo "Luna na foto")

1. **Entrada:** foto (até 1600 px), pedido opcional "remover tatuagens".
2. **Análise** (pod, poucos segundos):
   - LunaFaces: rostos, sexo e semelhança, para escolher a pessoa certa (já existe);
   - DWPose: mãos;
   - Florence/SAM2: cabelo e pessoa;
   - grounding de brinco/óculos na região da cabeça.
3. **Referência de cabeça:** uma master curada (frontal, sem maquiagem forte, brinco pequeno ou nenhum). Opcionalmente, a master mais próxima do ângulo da cabeça (yaw). [HIPÓTESE: escolher pelo ângulo melhora; testar]
4. **Recorte:** caixa que contém o rosto **e** o cabelo original inteiro (máscara de cabelo), mais o pescoço e os ombros (o modelo precisa ver onde a cabeça encaixa).
5. **Qwen 2511 + BFS V5:** ~22 s com o modelo carregado [TESTE]. No pod de 24 GB pode passar de ~100 s por descarregar o modelo [TESTE: 103 s em lote, ~170 s avulso].
6. **Composição local:** alinhamento → M → cor → degradê; fora de M = original.
7. **Checagens automáticas** (seção 7). Reprovação → 1 nova tentativa com outra semente, no máximo, e só se o problema for de geração (não de máscara).
8. **(Opcional) Tatuagem:** máscara mostrada → aprovada → recortes locais → composição → checagem.
9. **Entrega:**
   - a imagem é baixada pelo navegador **antes** de o pod desligar (já corrigido);
   - fica guardada no volume com telemetria.

---

## 6. Parâmetros a calibrar e como

| Parâmetro | Faixa | Como calibrar | Custa GPU? |
|---|---|---|---|
| Tamanho do recorte (×rosto, desce até ombros) | 3,0–4,5 | ver se o cabelo original inteiro cabe, nas fotos do conjunto de teste (máscara de cabelo) | não |
| Zona de M (raio em volta do rosto) | 0,6–1,2× o rosto | com as cabeças já geradas (benchmark + quarto) | **não** |
| Largura do degradê | 3–15 px a cada 1000 | idem, nota visual + métrica de emenda | **não** |
| Correção de cor: campo × Laplaciana × Poisson | 3 métodos | idem | **não** |
| Grão | igualar ao da pele vizinha, teto 2,5 | idem | **não** |
| Referência de cabeça | 2–3 masters candidatas | prova de conceito | sim (pouco) |
| Semente | 2 por foto | prova de conceito | sim |
| Tatuagem: método a/b/c, denoise | 0,25–0,5 | (b) LaMa na CPU primeiro; o resto na prova de conceito | parcial |

---

## 7. Aprovação automática e avaliação visual

**Critérios objetivos, definidos antes de rodar:**

| Medida | Como | Limite (calibrar na fase 0) |
|---|---|---|
| Fora da máscara intacto | diferença máxima dos pixels fora de M dilatado | **= 0** (por construção; se falhar, é bug) |
| Identidade | ArcFace antelopev2, média contra as 4 masters | ≥ 0,65 (o benchmark deu média 0,727) |
| Rosto da pessoa original | semelhança com o rosto original | ≤ 0,30 |
| Emenda | degrau de cor **através** da borda de M (3 px dentro × 3 px fora, só onde fora é pele ou fundo, não onde o cabelo novo cobre) | calibrar com imagens já julgadas por você |
| Resto do cabelo original | pixels com a cor do cabelo original na região de cabelo final | calibrar |
| Acessório novo | brinco/argola na região da cabeça: final × original | 0 brincos novos |
| Alinhamento | correlação ECC | ≥ 0,9 (como no benchmark) |
| Tatuagem (se pedida) | tinta restante na máscara; pixels alterados fora da máscara = 0; tom ΔE ≤ 3 | já existe no retoque |

**Avaliação visual:**
- folha com original × resultado, a 100% na cabeça e no pescoço;
- notas de 1 a 5 em 6 critérios: identidade, naturalidade, cabelo, borda, maquiagem/acessórios, preservação.

**Calibração sem custo:** você julga ~20 imagens que **já existem** (benchmark de 07/10, quarto de 07/10, V2/V3 de 08–09/10). Os limites saem dessas notas, para a métrica concordar com o seu olho antes de qualquer rodada nova.

---

## 8. Custo e consumo (estimativas a partir de medidas)

| Item | Valor | Base |
|---|---|---|
| Troca de cabeça com o modelo carregado | ~22 s | [TESTE] PRO 4500 |
| Em lote no estúdio (PRO 4000 24 GB) | ~103 s/img (US$ 0,016) | [TESTE] Persona Engine 05/10 |
| Avulso no estúdio | ~170 s (~US$ 0,027) | [TESTE] memória 04/10 |
| Ligar o pod | 1,5–6,5 min (US$ 0,015–0,06) | [TESTE] |
| Composição e checagens | segundos, na CPU | [TESTE] benchmark |
| VRAM | ~20–30 GB (fp8) | [TESTE] pico de 29,8 GB na PRO 4500 |
| **Modo cabeça, por foto (lote)** | **~US$ 0,02–0,03** | estimativa |
| V3.1 atual, por foto | ~US$ 0,08–0,09 | [TESTE] 8–11 min |
| Tatuagem (opcional) | +1–3 min se usar SDXL (+ download de 9,45 GB) ou ~30 s com Qwen/LaMa | [TESTE]/[HIPÓTESE] |

---

## 9. Riscos, limitações e quando vai falhar

1. **Corpo não é o da Luna** no modo padrão. É limitação de projeto, não defeito.
2. **Tom de pele rosto × corpo:** a Luna é morena; se o corpo da foto for muito claro, igualar o pescoço deixa o rosto mais claro que a Luna canônica. O contrário deixa uma "máscara". Pode exigir escolha por foto [HIPÓTESE].
3. **Perfil extremo, rosto coberto (celular na frente), close muito grande:** o BFS cai.
   - [TESTE] no Pack, close de perfil comendo ficou com cabelo claro.
4. **Mãos no cabelo ou no rosto:** a máscara exclui as mãos, mas se o Qwen redesenhar dedos que cruzam o cabelo, sobra uma junção difícil [HIPÓTESE].
5. **Cabelo longo ou preso de forma muito diferente** (coque × cabelo solto): a região de troca cresce e encosta na roupa/colo.
6. **Várias pessoas:** funciona com escolha pelo LunaFaces [TESTE no Pack]. Rosto de outra pessoa dentro do recorte precisa ficar fora de M (já existe).
7. **Rejuvenescimento e maquiagem do BFS:** mitigados pela referência, não eliminados [TESTE].
8. **Tatuagem em mãos:** continua sem solução comprovada [TESTE testes 2–7].
9. **Infraestrutura:** disco de rede, pods duplicados e ociosidade (seção 1.3).
10. **Direitos de uso das fotos de origem.** No modo "Luna na foto" o corpo, a pele e a lingerie são da **pessoa real** da foto. Para fotos íntimas ou sensuais, isso significa usar o corpo de alguém sem saber se ela consentiu.
    - O repositório do BFS pede para não usar com pessoas que não consentiram [DOC].
    - A recomendação é usar só fotos com direito de uso: suas, de modelos contratadas ou banco de imagens com licença.
    - Isso vale para qualquer arquitetura, porque a pose e o corpo vêm sempre da foto.
11. **Licença dos modelos de rosto (vale para o projeto inteiro, não só para esta proposta).** Os pesos do InsightFace (antelopev2) são para pesquisa não comercial [DOC]. Eles estão em produção hoje: no LunaFaces (escolha do rosto), no InstantID e na métrica de identidade.
    - Se o uso for comercial, é preciso licenciar com o InsightFace ou trocar por pesos liberados. Uma alternativa citada pela comunidade é YuNet + SFace (OpenCV Zoo), ainda a conferir.
    - A troca de cabeça BFS em si (Qwen Apache 2.0 + BFS MIT) não depende do InsightFace; só a escolha do rosto e a medida dependem.

---

## 10. Plano por etapas (cada etapa só começa com a sua aprovação)

### Fase 0: local, **custo zero** (sem GPU, sem download pago)
1. Montar o **conjunto de teste fixo**, umas 10 fotos:
   - as 3 de hoje (rua, quarto/lingerie, espelho);
   - porta, espelho braço, varanda óculos, quarto tatuagem;
   - 3 fotos do Pexels (casal, perfil, cabelo preso).
2. **Você julga ~20 imagens já existentes** → limites das métricas (seção 7).
3. Com as **cabeças já geradas** pelo BFS (benchmark de 07/10 + quarto), comparar na CPU:
   - composição atual (retângulo) × `compor_cabeca` × Laplaciana × Poisson;
   - largura do degradê;
   - máscara de cabelo com e sem segmentação.
   - Sai uma folha visual + números.
4. Escolher a referência de cabeça entre as masters existentes (sem maquiagem forte, brinco pequeno). É inspeção visual, sem custo.
5. LaMa na CPU nos recortes de tatuagem do quarto: só o pré-preenchimento.
6. Entregar o resultado da fase 0 e pedir autorização para a fase 1.

### Fase 1: prova de conceito do modo cabeça (**teto US$ 0,35**)
- 10 fotos × 2 referências × 1 semente no estúdio, em lote, com:
  - sondagem antes;
  - keepalive;
  - download antes de desligar;
  - trava no navegador.
- **Aprovação:**
  - ≥ 8/10 aprovadas no seu olho;
  - 10/10 com "fora intacto = 0";
  - identidade média ≥ 0,70;
  - 0 brincos novos.
- Lado a lado com a V3.1 atual nas mesmas fotos (as V3.1 já existem para 3 delas; não precisa gerar de novo).

### Fase 2: tatuagem opcional (**teto US$ 0,20**)
- Só nas fotos com tatuagem, métodos a/b/c no mesmo pod.
- **Aprovação:** tom ΔE ≤ 3, 0 px alterados fora da máscara, nota visual ≥ 4.

### Fase 3: integração na branch (sem custo de GPU)
- Rota `/v2/head` usando `head_swap` + composição nova.
- Na tela, a escolha "Luna na foto (recomendado)" × "Luna inteira (roupa redesenhada)".
- Testes automatizados.
- A V3.1 continua disponível; nada é apagado (rollback = trocar o padrão).

### Fase 4: comparação final e publicação
- Comparação com a versão no ar → sua aprovação → publicação.

### Paralelo (infraestrutura, sem custo)
- Lista de pods parados para você apagar.
- Sondagem do disco antes de qualquer lote.
- Download obrigatório antes de desligar.

---

## Fontes externas consultadas
- BFS Best Face Swap (espelhos do README e ComfyUI Wiki): https://huggingface.co/mr2along/BFS/blob/main/README.md · https://comfyui-wiki.com/ko/news/2026-09-25-bfs-qwen-image-2-1-head-swap
- Qwen-Image-Edit 2511 (licença, consistência): https://the-decoder.com/qwen-updates-image-editing-model-with-better-character-consistency/
- Deriva de pixels e crop/stitch no Qwen Edit: https://lilting.ch/en/articles/qwen-image-edit-pixel-perfect-referencelatent · https://www.runcomfy.com/comfyui-nodes/ComfyUI-Inpaint-CropAndStitch
- InfiniteYou × PuLID: https://github.com/bytedance/InfiniteYou · comparação de comunidade: https://myaiforce.com/hyperlora-vs-instantid-vs-pulid-vs-ace-plus/ · ACE++: https://github.com/ali-vilab/ACE_plus
- BiRefNet (ComfyUI): https://docs.comfy.org/tutorials/utility/remove-background-birefnet
- SAM 3 (ComfyUI, licença): https://www.runcomfy.com/comfyui-nodes/ComfyUI-SAM3 · https://huggingface.co/facebook/sam3/blob/main/LICENSE
- Sapiens (licença não comercial): https://huggingface.co/facebook/sapiens-seg-1b/blob/96fdb8a0a1829b88bff0315dac1b1652fd4ea67e/README.md
- InsightFace model zoo (pesos não comerciais): https://github.com/deepinsight/insightface/blob/e68b8b076fb11e9f75c87244d234385f0f318181/model_zoo/README.md
- SegFormer B2 clothes (classes): https://huggingface.co/teerapongsungsut/segformer_b2_clothes
- Differential Diffusion / LanPaint: https://comfy.icu/node/DifferentialDiffusion · https://dev.co/ai/frameworks/lanpaint
- Mistura Laplaciana × Poisson: https://www.cs.princeton.edu/courses/archive/fall10/cos526/papers/perez03.pdf · https://inst.eecs.berkeley.edu/~cs194-26/fa17/upload/files/proj3/cs194-26-abz/
- Remoção por inpainting (LaMa, limites em textura): https://arxiv.org/pdf/1909.06399 · https://wacv.thecvf.com/virtual/2026/poster/606
