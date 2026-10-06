# Luna V2 travada: como montar em outra máquina

Este guia monta a geração **V2** da Luna em outra máquina:
- RealVisXL + LoRA SDXL da Luna;
- estilo de selfie de celular;
- 3 passadas de rosto (a 1ª com InstantID) e 2 de corpo, com rollback.

A descrição completa do que está travado fica em [`luna_v2_travada.json`](luna_v2_travada.json).

> **Estado:** experimental. Está na branch `feature/persona-v2-realvis-lustify`, não está na main e não substitui a V1 do site.
> Rosto travado: `luna-face-v2.0`, impressão `14e6af91fd928300…`.

---

## 1. Máquina

| Item | Mínimo | Testado |
|---|---|---|
| GPU NVIDIA | 16 GB de VRAM | RTX PRO 4000 Blackwell 24 GB |
| Disco livre | ~15 GB (modelos) | — |
| Software | Python 3.12, ComfyUI recente | ComfyUI v0.30.0, PyTorch 2.10 + CUDA 12.8 |

- **VRAM medida:** 10,3 GB de pico sem o InstantID. Com o InstantID não foi medida separadamente; a estimativa é de +3 GB.
- **Tempo:** ~145–195 s por imagem com as 5 passadas.

## 2. Arquivos do projeto (branch `feature/persona-v2-realvis-lustify`)

| Caminho | O que é |
|---|---|
| `backend/` | motor (núcleo, adapters do ComfyUI, validação) |
| `config/persona_engine.json` | negativo global e regras da V1, também usados pela V2 |
| `config/persona_engine_v2.json` | **receita travada**: modelo, LoRA, passadas, InstantID, estilo, critérios de aceite e trava |
| `config/v2_cenas_influencer.json` | as 10 cenas padrão de influencer |
| `workflows/realvis-base.json`, `realvis-face-pass.json`, `realvis-face-pass-instantid.json`, `realvis-body-pass.json` | grafos do ComfyUI |
| `personas/luna/persona_sheet.json`, `persona.json` | ficha e perfil da Luna |
| `personas/luna/references/*.png` | **4 fotos master** (fora do git; copiar do volume do pod ou do pacote da V1) |
| `personas/luna/style_refs/realismo_ref_01.webp` | foto de referência do estilo (fora do git) |
| `comfyui_nodes/luna_faces/` | nó LunaFaces (rosto, idade e semelhança) |
| `scripts/benchmark_v2_multipass.py` | gerador (cenas do arquivo ou `--cena`) |

**Conferência das masters:** o motor confere o sha256 antes de gerar. Os valores estão em `luna_v2_travada.json` → `masters`. A `master_face` tem `1c431d3c…`.

## 3. Modelos

| Pasta em `ComfyUI/models/` | Arquivo | Tamanho | sha256 | Fonte |
|---|---|---|---|---|
| `checkpoints` | `RealVisXL_V5.0_fp16.safetensors` | 6,94 GB | `6a35a785…` | https://huggingface.co/SG161222/RealVisXL_V5.0/resolve/main/RealVisXL_V5.0_fp16.safetensors |
| `instantid` | `instantid_ip-adapter.bin` | 1,69 GB | `02b3618e…` | https://huggingface.co/InstantX/InstantID/resolve/main/ip-adapter.bin |
| `controlnet` | `instantid_controlnet.safetensors` | 2,50 GB | `c8127be9…` | https://huggingface.co/InstantX/InstantID/resolve/main/ControlNetModel/diffusion_pytorch_model.safetensors |
| `insightface/models/antelopev2/` | 5 arquivos `.onnx` | 360 MB | — | https://github.com/deepinsight/insightface/releases/download/v0.7/antelopev2.zip (os `.onnx` vão direto na pasta) |
| `loras` | `lunavox_sdxl_v1.safetensors` | 170 MB | `0a58a72e…` | **não existe para download**: está em `Downloads\loras.zip` (pasta `loras/`) e no volume do pod (`ComfyUI/models/loras/`) |

Os sha256 completos estão em `config/persona_engine_v2.json`. Os pesos do DWPose são baixados sozinhos no primeiro uso pelo `comfyui_controlnet_aux`.

## 4. Nós extras do ComfyUI

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/cubiq/ComfyUI_InstantID.git
git clone https://github.com/Fannovel16/comfyui_controlnet_aux.git
pip install -r comfyui_controlnet_aux/requirements.txt
pip install insightface onnxruntime numpy
cp -r /caminho/do/projeto/comfyui_nodes/luna_faces .
```

Ligue o ComfyUI e confira no log que os nós `ApplyInstantID`, `LunaFaces` e `DWPreprocessor` carregaram.

## 5. Motor

O script espera esta pasta de trabalho (`V2_ROOT`):

```
V2_ROOT/
├── backend/            (do projeto)
├── config/             (do projeto)
├── workflows/          (do projeto)
├── benchmark_v2_multipass.py
└── personas_run/luna/
    ├── persona.json
    ├── persona_sheet.json
    └── references/     (as 4 masters)
```

Instale as dependências do motor:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
```

Gere as 10 cenas padrão:

```bash
V2_ROOT=$PWD python benchmark_v2_multipass.py --cenas-arquivo config/v2_cenas_influencer.json --saida resultado.json --autorizado 1.0
```

Ou gere uma cena própria, no formato `semente|rotulo|texto em inglês`:

```bash
V2_ROOT=$PWD python benchmark_v2_multipass.py --cena "7501|praia|front camera selfie, phone held at arm's length, at the beach in the afternoon, wearing a white tank top" --autorizado 1.0
```

- **ComfyUI:** o script usa o que está em `http://127.0.0.1:8188`.
- **`--autorizado`:** é o teto de custo da trava de orçamento. Em máquina própria, o custo vem do preço por hora configurado no script (US$ 0,57); ajuste ou ignore.
- **Saída:** um JSON com as notas de cada passada, rollbacks, validação final e `persona_drift`. As imagens ficam na pasta `output` do ComfyUI (`luna_v2_*`).

## 6. Como saber se está igual ao original

1. **A trava confere:** ao carregar, `config/persona_engine_v2.json` recalcula a impressão. Se algo da receita mudou, aparece `A receita do rosto da persona esta TRAVADA e foi alterada` e nada é gerado.
2. **Rosto:** uma imagem final com `rosto_final` ≥ 0,70 é a Luna. A referência aprovada teve 0,827 → 0,817 → 0,803 nas passadas de rosto. Abaixo de 0,70, a imagem é marcada `PERSONA_DRIFT`; refaça com outra semente.
3. **Testes:** dentro de `backend/`, rode `python -m pytest -q`. Os testes da trava estão em `tests/test_v2_identity_lock.py`.

## 7. Destravar (só de propósito)

Para mudar a receita (peso do InstantID, denoise, LoRA, modelo...):
1. Edite `config/persona_engine_v2.json`.
2. Mude `identity_lock.version`, por exemplo para `luna-face-v2.1`.
3. Recalcule `identity_lock.fingerprint` com `app.core.generation.v2_config.identity_fingerprint`.
4. Registre o motivo.

Estilo, cenas e negativo comum podem mudar sem destravar.
