# Inventário (item 0 do PROMPT_correcoes_sistema_Luna), 2026-10-07

Feito sem pod e sem mudar nada no sistema.

## 1. Erros abertos, com evidência

| # | Erro | Evidência | Item que resolve |
|---|---|---|---|
| E1 | Tatuagens: a mão e o antebraço direitos viraram textura de **pelo alaranjado** | `generated/v2/retoque/foto1/relatorio.json`: tom ΔE 6,33 e blocos 0,013, REPROVADO. Recorte `t7_100_braco.png` | 4 (passe 2 do braço). **Falta o `PROMPT_passe2_braco.md`** |
| E2 | Semelhança baixa na troca de cabeça (cena da massagem 0,216 contra 0,55 no estúdio) | **Não tenho essa evidência:** não há cena de massagem nem "0,216" no projeto ou nas pastas desta máquina | 2b / 3 |
| E3 | O ComfyUI caiu 3 vezes hoje (Qwen Q3 GGUF local) | **Não há ComfyUI local nesta máquina** (ver item 2): sem log e sem evento para ler | 1 |
| E4 | Retângulo vermelho e recorte da referência fixos (96 px e 47%) | `backend/app/services/person_swap.py`: `RING_MARGIN = 96`, `RING_THICKNESS = 8`, `PERSONA_HEAD_FRACTION = 0.47` | 2a / 2b |
| E5 | Saída do Qwen entregue (quase) inteira | `person_swap.py`: `qwen_swap_params(... recreate ...)` manda a foto inteira quando o recorte passa de 80%. Não existe `compor_cabeca` com ECC, correção de cor no anel e checagem | 2d / 2e |
| E6 | **Fora do prompt:** cada vez que o pod liga, o site cria um **pod novo** (o host anterior fica sem GPU) e o antigo fica parado | Logs do wake: 31l94dzd5ty40t → n59gdx6zrjcns6 → tofcnishjfzoaz → t18oeekhfzxkf7 → nis05urxob18j2 | **Nenhum.** Preciso perguntar |
| — | Resolvidos na rodada do teste 7 | Ombro (flor), alto do braço e argola: limpos (`t7_100_ombro.png`, `t7_100_orelha.png`) | — |

## 2. Onde roda cada etapa hoje

| Etapa | Onde | Por quê |
|---|---|---|
| Troca de cabeça/pessoa (Qwen-Image-Edit 2511 fp8 + Lightning 8 passos + BFS head V5, retângulo vermelho) | **Pod** (RTX PRO 4000 24 GB, EU-RO-1). Modelos **já ficam no volume** `luna-models-ro` | Precisa de cerca de 20 GB de VRAM. Esta máquina tem **Intel UHD (sem NVIDIA) e 7,7 GB de RAM** |
| Detecção (Florence-2, SAM2, InsightFace/LunaFaces) | **Pod** (nós do ComfyUI) | Os modelos e os nós estão lá |
| Tatuagens: máscara, recortes, pré-preenchimento, composição e checagem (`retoque_tatuagens.py`) | **Esta máquina**, CPU (venv com OpenCV/SciPy) | Não precisa de GPU |
| Tatuagens: modelo (RealVisXL + LoRA + ControlNet depth) | **Pod**, por túnel SSH | GPU. **Baixa 9,45 GB por sessão**, porque o volume está quase cheio |

## 3. Download por sessão × volume (item 5 da regra do pod)

- **Medido:** 9,45 GB (RealVisXL + ControlNet union) baixam em 56 a 177 s, mediana de cerca de 90 s.
  - Na mediana: **~US$ 0,014 por sessão**.
  - No pior caso: US$ 0,028.
- **Volume de rede do RunPod:** cerca de US$ 0,07 por GB/mês (conferir o preço atual no painel). 9,45 GB custam **~US$ 0,66/mês**.
- **Empate:** entre 24 e 47 sessões por mês.
  - Nos últimos 2 dias foram cerca de 12 sessões. Nesse ritmo, **guardar no volume sai mais barato**.
  - O volume (165 GB) está quase cheio: é preciso aumentar (+10 GB ≈ US$ 0,70/mês) ou liberar modelos que não usamos. Liberar exige apagar, e quem roda os comandos é você.
- **Qwen:** já está no volume, não é baixado por sessão.
