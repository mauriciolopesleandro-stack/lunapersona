# Benchmark de troca de cabeça numa sessão única (PROMPT_modelo_fase_unica.md)

## Variantes (7)
| | Modelo | LoRAs | Produção? |
|---|---|---|---|
| A | 2511 Q3_K_M (GGUF, unsloth) | Lightning 8 + BFS head V5 | sim |
| B | 2511 fp8 (o do estúdio) | Lightning 8 + BFS | sim |
| C | 2511 NVFP4 (ANXS1) | Lightning 8 + BFS | se empatar com B (sem cu130 não acelera) |
| D | FireRed 1.1 (bf16 → fp8) | Lightning FireRed v1.2 + BFS | sim (Apache 2.0) |
| E | FireRed 1.1 | Lightning FireRed v1.2 | sim |
| F | Qwen-Image-2.1 int8 (máscara da cabeça) | — | **não** (licença de pesquisa) |
| G | 2511 fp8 | Lightning 8 + BFS + LoRA da Luna (`luna_qwen_2511_v1`) | sim |

- **Matriz:** cenas 5, 9 e 14 do pack Pexels × sementes 1234 e 5678 = **42 execuções**. Sem a cena de massagem, por decisão do usuário.
- **Downloads:** 88,7 GB, conferidos com HEAD.

## Fluxo
1. **Sem pod:** `preparar.py` (lv venv), `validar_workflows.py` (ComfyUI v0.37 local na CPU com arquivos vazios: os 7 passaram), ensaio com `mock_bench.py` + `rodar_sessao.py` + `compor_cabeca.py`.
2. **Pod (uma vez):**
   - o estúdio roda `criar_pod_bench.sh`: cria o `luna-bench` (RTX PRO 4500 32 GB, 160 GB de disco) e se desliga;
   - o `luna-bench` roda `sessao_pod.sh` sozinho: ComfyUI v0.37 separado na porta 8189, downloads em paralelo e painel na 8188;
   - ele desliga com `/fim` ou pelo teto de 75 min.
3. **Local:** `rodar_sessao.py --url https://ID-8189.proxy.runpod.net --status https://ID-8188.proxy.runpod.net` (agrupado por modelo, sincroniza as saídas, manda `/fim` no `finally`).
4. **Sem pod:** `compor_cabeca.py --bench ...` gera `tabela.json` (semelhança na final composta e checagem).

**Não abrir o site durante a sessão:** o site liga o estúdio e desliga o `luna-bench`.
