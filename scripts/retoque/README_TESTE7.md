# Teste 7 — remoção local das tatuagens da Luna (sem trocar o modelo)

Você vai ajustar o pipeline de fotos da Luna seguindo a direção do Teste 7 do `Resumo_Tecnico_Ajustes_Fotos_Luna.docx`. O modelo de geração, o checkpoint, a identidade da Luna e o ControlNet **não mudam**. O que muda é tudo em volta do modelo: onde ele trabalha, o que ele recebe e como o resultado volta para a foto.

---

## REGRA DO POD (vale para tudo)

1. **Tudo que não precisa de GPU roda antes, com o pod desligado.** Isso inclui máscaras, recortes, workflow, validações e ensaio completo com o ComfyUI falso.
2. **O pod liga uma única vez por rodada.** Ele recebe todos os recortes de todas as fotos pendentes, processa, devolve e **desliga**. Use os mesmos comandos de ligar/desligar que já usamos.
3. **O pod desliga mesmo se der erro.** O `comfy_pod.py` faz isso num bloco `finally`, inclusive em timeout e Ctrl+C. Depois da rodada, confirme pelo nosso método de sempre que o pod está parado.
4. **Nunca depure com o pod ligado.** Se falhar: desligue, leia o erro, corrija offline, valide com `--seco` e só então rode de novo. Só as regiões pendentes são reprocessadas.
5. **Agrupe.** Um eventual segundo passe (`passe2/`) vai na próxima sessão junto com outras fotos pendentes, não numa sessão só para ele.
6. **Informe sempre os minutos de pod usados.**

---

## Anexos

| Arquivo | O que é | Precisa de pod? |
|---|---|---|
| `retoque_tatuagens.py` | Faz quatro coisas sem GPU: (1) detecção da tinta e máscara orgânica; (2) recortes pré-preenchidos para o modelo; (3) correção de cor/grão, degradê e composição; (4) checagem automática. | Não |
| `comfy_pod.py` | Liga o pod, envia todos os recortes ao ComfyUI pela API, baixa os resultados, desliga e compõe. | Só ele usa |
| `mock_comfy.py` | ComfyUI falso para ensaiar o fluxo inteiro sem pod. | Não |
| `Resumo_Tecnico_Ajustes_Fotos_Luna.docx` | Histórico dos testes 2–6b e critérios. | — |

Dependências (CPU): `pip install opencv-python-headless numpy scipy` e, opcional, `rembg[cpu]` para `--pessoa auto` (baixa ~170 MB uma vez). `comfy_pod.py` usa só a biblioteca padrão.

---

## Diagnóstico (por que os testes 2–6b falharam)

Medido na foto original e na máscara usada no Teste 6 (overlay vermelho):

- **O detector antigo confunde e deixa buracos.**
  - Marca o cabelo acima do ombro e a dobra da axila como tinta, e deixa de fora quase toda a flor do ombro.
  - Isso explica a tatuagem do ombro que nunca sai e o cabelo virando bloco de pele.
  - No braço, a máscara tem buracos e não pega a parte direita do antebraço.
- **Tinta se separa de sombra e cabelo pela cor.**
  - Em Lab, a tinta tem quase zero de saturação (a e b entre 0 e 8). A pele tem a ≈ 11–15 e b ≈ 13–22.
  - Sombra, cabelo e pinta continuam quentes; tinta fica cinza/azulada.
  - Regra usada: perdeu saturação **e** está mais escura que a pele vizinha, com histerese.
- **A cor dessas fotos vem em blocos JPEG de 8–16 px.** Medir cor sem suavizar, ou compor com máscara em resolução baixa/latente, gera os quadrados.
- **Pontinhos brancos** são ilhas de pele dentro da tatuagem que ficaram fora da máscara. Quando a reconstrução sai mais escura ou alaranjada, viram pontos claros.
- **Mancha clara** é deriva de tom do modelo, que não era corrigida localmente.
- **Borda dura** vem da composição binária, sem degradê.
- **A argola grande** entra por regeneração/integração global perto do rosto.

---

## O que já está pronto e como foi validado

`retoque_tatuagens.py` faz, sem GPU:

1. **Proteção automática.** Tudo que difere entre a ORIGINAL e a BASE (rosto e cabelo da Luna já aplicados) fica travado. Também ficam fora: roupa preta, fundo claro frio e, com `--pessoa`, tudo fora da silhueta. Uma faixa fina colada no top fica intocada de propósito, para a borda dele não serrilhar.
2. **Máscara orgânica.** A tinta detectada na original é refinada:
   - fechamento dos traços;
   - ilhas pequenas preenchidas;
   - margem de 3 px na escala de 780 px;
   - descarte de componentes sem pele em volta (cabelo) e de objetos frios (cadeira, jeans).
3. **Um recorte quadrado por grupo de tatuagens**, com contexto, ampliado para 1024 px no lado maior e com dimensões múltiplas de 64.
4. **Pré-preenchimento.** A tinta sai do recorte antes do modelo: preenchimento suave a partir da pele limpa vizinha, mais grão igual ao dela. O modelo não "vê" a tatuagem, então não sobra fantasma.
5. **Integração.**
   - Correção de cor/luz medida no anel de pele limpa e levada suavemente para dentro.
   - Grão igualado ao original.
   - Degradê de 8 px.
   - Composição só em pele, em resolução cheia, nunca com a máscara latente.
6. **Checagem automática** que reprova sozinha (critérios abaixo). Se sobrar tinta, o `compor` cria `<pasta>/passe2/` só com o que sobrou.

Validação feita:

- A checagem **reprova os testes 6/6b**: tinta residual 11–27%, mancha de tom ΔE 7–14, pontinhos 7–14× a pele vizinha.
- Um modelo simulado que deixa a pele de propósito mais clara, alaranjada e lisa:
  - colado direto, reprova (tom ΔE 6,5);
  - passando pelo pipeline, **aprova** (tinta residual < 1%, tom ΔE ≈ 1,8, sem blocos).
- O `comfy_pod.py` foi testado contra o ComfyUI falso nestes casos:
  - rodada normal;
  - duas fotos na mesma sessão;
  - retomada (não liga o pod se não há pendências);
  - erro no workflow no meio da rodada (desligou);
  - ComfyUI que não sobe (desligou após a espera);
  - workflow exportado no formato errado (barrou antes de ligar).

**Não testado:** o seu workflow real e o pod real. Os limiares foram calibrados numa cópia comprimida pelo WhatsApp (779×1240). Os parâmetros escalam com a largura, mas confira o debug nas originais.

---

## Tarefas, em ordem

### 1. [SEM POD] Base composta
- A base é a foto original com rosto e cabelo da Luna aprovados (Teste 5) compostos por cima, como no Teste 6.
- Mesmo tamanho e enquadramento da original, em PNG.
- **Não** use uma saída regenerada inteira. Se mais da metade da imagem diferir, o script avisa e quase tudo fica protegido.

### 2. [SEM POD] Máscaras e recortes, para cada foto
```
python retoque_tatuagens.py preparar --original foto1.jpg --base foto1_base.png --pessoa auto --trabalho foto1/
```
Abra `foto1/debug_mascara.jpg`:

- **Legenda:** vermelho = tinta; contorno amarelo = área reconstruída; caixa = recorte (`m` = vai ao modelo, `c` = resolvido sem modelo).
- **Precisa cobrir:** a flor do ombro inteira e o braço todo, incluindo a rosa da mão.
- **Não pode pegar:** cabelo, dobra da axila, borda do top ou cadeira.
- **Argola:** pinte uma máscara simples (branco sobre preto, do tamanho da foto) cobrindo a argola mais 3–4 px em volta e passe `--incluir argola.png`.
- **Colar fino:** se ele cruzar uma tatuagem, use `--proteger colar.png`.
- **Ajustes:** use `--config ajustes.json` com campos de `Config`. Os principais:
  - `deficit_forte`/`deficit_fraco`, `escuro_forte`: sensibilidade à tinta.
  - `margem`, `raio_fechamento`: tamanho da máscara.
  - `b_min_tinta`: baixe para -10 se a tatuagem for azul forte.
  - `L_min`: baixe com cuidado para tinta preta muito escura, conferindo que o cabelo não entra.
  - `lado_modelo`: 768 se o checkpoint for SD1.5.

### 3. [SEM POD] Workflow do ComfyUI só para as regiões
Parta do workflow atual (mesmo checkpoint, mesma identidade/LoRA da Luna, mesmo ControlNet de profundidade e mesmo sampler). Ajuste assim:

- **Entrada:** um único `LoadImage`, que recebe o recorte pré-preenchido.
- **Profundidade:** o mesmo pré-processador de hoje, aplicado nesse recorte, alimentando o ControlNet Depth com strength 0.6–0.8, start 0.0 e end 0.8–1.0.
- **Máscara:** um único `LoadImageMask`, canal `red`.
- **Latente:** `VAEEncode` do recorte → `SetLatentNoiseMask` com a máscara.
  - **Não** use `VAEEncodeForInpaint`: ele apaga a área e exige denoise 1.0, que traz de volta geometria inventada.
  - Se o checkpoint for de inpaint, `InpaintModelConditioning` também serve.
  - Se o fluxo atual usa outro caminho de inpaint (ex.: Flux Fill), mantenha-o, garantindo: entrada = recorte pré-preenchido, máscara = `LoadImageMask`, denoise moderado se o caminho permitir.
- **Sampler:** um único `KSampler`, denoise 0.55–0.75 (comece em 0.65), seed fixa.
- **Prompt positivo:** `natural bare skin, realistic skin texture, same lighting, photo`.
- **Prompt negativo:** `tattoo, ink, drawing, lettering, text, jewelry, earring, hoop earring, bracelet, ring, plastic skin, blurry`.
- **Saída:** `VAEDecode` → um único `SaveImage` com o recorte inteiro.
- **Proibido neste workflow:** `ImageCompositeMasked`, upscaler, face detailer, face swap/restore e qualquer integração global. Quem compõe é o script.
- Exporte em formato API (Workflow → Export (API)) como `inpaint_regiao_api.json`.

### 4. [SEM POD] Ensaio completo com o ComfyUI falso
Use uma cópia da pasta, para não deixar `regiao_XX_saida.png` de teste na pasta real:
```
cp -r foto1 ensaio_foto1
python mock_comfy.py --gerar-workflow wf_teste_api.json
python mock_comfy.py &          # ComfyUI falso em http://127.0.0.1:8188
python comfy_pod.py --trabalho ensaio_foto1/ --workflow wf_teste_api.json --url http://127.0.0.1:8188 --compor
```
Tem que terminar com **APROVADO**. Depois valide o workflow real, ainda sem pod:
```
python comfy_pod.py --trabalho foto1/ foto2/ --workflow inpaint_regiao_api.json --seco
```

### 5. [COM POD, uma vez] Rodada real de todas as fotos preparadas
```
python comfy_pod.py --trabalho foto1/ foto2/ ... --workflow inpaint_regiao_api.json \
  --url <URL do ComfyUI no pod> \
  --ligar "<comando que já usamos para ligar o pod>" \
  --desligar "<comando que já usamos para desligar o pod>" \
  --denoise 0.65 --seed 1234 --timeout-min 20 --compor
```
- Ajuste `--timeout-min` ao lote: boot mais cerca de 1 min por região. Ele é o teto absoluto com o pod ligado.
- Se houver mais de um `LoadImage`, `LoadImageMask` ou `SaveImage`, passe `--no-imagem`, `--no-mascara` e `--no-saida` com os IDs.
- Se o proxy exigir token, use `--cabecalho "Authorization: Bearer ..."`.
- Ao terminar, confirme que o pod está parado.

### 6. [SEM POD] Avaliação
Para cada foto, olhe:
- `relatorio.json`;
- `debug_final.jpg`, onde magenta = tinta residual e verde = arestas retas novas;
- recortes a 100% do braço, da mão e do ombro, antes e depois.

Se reprovar:
- **Tinta residual:** inclua `fotoX/passe2/` na próxima sessão de pod.
- **Mancha de tom ou emenda:** baixe o denoise ou reveja o prompt negativo.
- **Blocos:** confira se o workflow não tem composição/upscale próprio e se nada foi salvo em JPEG entre etapas.
- **Pontinhos:** aumente `area_ilha_max`.

Ajuste offline, valide com `--seco` e só então volte ao pod.

---

## Critérios de aprovação (checagem automática + olho)

| Medida | Limite | O que pega |
|---|---|---|
| `mudanca_fora_da_area` | 0 | rosto, cabelo, top, fundo ou pele limpa alterados |
| `tinta_residual` | ≤ 0,03 | tinta que sobrou (fração da tinta original) |
| `tom_deltaE` | ≤ 4 | mancha clara, escura ou alaranjada |
| `emenda_deltaE` | ≤ 4 | halo na faixa do degradê |
| `blocos` | ≤ 0,004 | quadrados e arestas retas novas |
| `pontinhos_relativo` | ≤ 2,5 | pontinhos em relação à pele vizinha |

Além disso, valem os critérios do resumo técnico:
- parecer foto real da Luna;
- rosto igual ao aprovado;
- mãos e braços na mesma posição e geometria;
- top e fundo intactos;
- nenhuma joia nova.

O indicador de blocos foi calibrado com poucos exemplos. Na dúvida, o olho decide e você me reporta.

---

## Não fazer

- Trocar modelo, checkpoint ou identidade da Luna.
- Regenerar a foto inteira ou rodar integração global no final.
- Mexer no rosto aprovado.
- Compor com máscara redimensionada do latente.
- Salvar intermediários em JPEG; use sempre PNG.
- Deixar o pod ligado esperando, testando ou depurando.
- Ligar o pod para uma foto só se houver outras pendentes que possam ir junto.

---

## O que me devolver

1. O `inpaint_regiao_api.json` final e a lista do que mudou em relação ao workflow anterior.
2. Os comandos exatos usados e os minutos de pod de cada sessão.
3. Por foto: `relatorio.json`, `debug_mascara.jpg`, `debug_final.jpg` e recortes a 100% (braço, mão, ombro) antes e depois.
4. Qualquer ajuste de `Config` feito e o motivo.
