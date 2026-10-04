#!/usr/bin/env bash
# Modelos do caminho rapido (pesquisa de mercado, 2026-10):
#  - Z-Image Turbo (Tongyi/Alibaba, Apache 2.0): cena em 8 passos. Versao int8
#    (6.2 GB) e leitor de texto Qwen3-4B fp4 (3.5 GB) - o volume de 165 GB
#    estava cheio; o VAE e o ae.safetensors do FLUX que ja esta no volume.
#  - FLUX.2 [klein] 4B fp8 (Apache 2.0, 3.8 GB) + VAE do FLUX.2: troca de
#    cabeca rapida, divide o leitor de texto com o Z-Image (cabem juntos nos 24 GB).
#  - LoRAs BFS Best Face Swap (MIT): head V5 para o Qwen-Image-Edit 2511 (que ja
#    esta no volume, scripts/setup_qwen_edit.sh) e head V1 para o Klein 4B.
#
# Roda uma vez no luna-models-ro. Nao apaga nada. O df do /workspace mostra o
# disco da RunPod inteiro, nao a cota do volume: se a cota estourar, o download
# falha e o script lista os maiores arquivos para decidir o que tirar.
set -uo pipefail

COMFY=/workspace/runpod-slim/ComfyUI
PY="$COMFY/.venv-cu128/bin/python"
M="$COMFY/models"

log() { echo "[setup_fast] $*"; }

# destino|repositorio|arquivo no repositorio
FILES=(
  "diffusion_models|Comfy-Org/z_image_turbo|split_files/diffusion_models/z_image_turbo_int8_convrot.safetensors"
  "text_encoders|Comfy-Org/z_image_turbo|split_files/text_encoders/qwen_3_4b_fp4_mixed.safetensors"
  "diffusion_models|black-forest-labs/FLUX.2-klein-4b-fp8|flux-2-klein-4b-fp8.safetensors"
  "vae|Comfy-Org/flux2-dev|split_files/vae/flux2-vae.safetensors"
  "loras|Alissonerdx/BFS-Best-Face-Swap|bfs_head_v5_2511_merged_version_rank_16_fp16.safetensors"
  "loras|Alissonerdx/BFS-Best-Face-Swap|bfs_head_v1_flux-klein_4b.safetensors"
)
[ -f "$M/vae/ae.safetensors" ] || log "AVISO: falta models/vae/ae.safetensors (VAE do FLUX, usado pelo Z-Image)"

failed=0
for entry in "${FILES[@]}"; do
  IFS="|" read -r dest repo path <<< "$entry"
  name="$(basename "$path")"
  mkdir -p "$M/$dest"
  if [ -f "$M/$dest/$name" ]; then
    log "ja existe: $dest/$name"
    continue
  fi
  log "baixando $name"
  if ! "$PY" - "$repo" "$path" "$M/$dest" <<'EOF'
import os, shutil, sys, time
from huggingface_hub import hf_hub_download
repo, path, dest = sys.argv[1:4]
tmp = os.path.join(dest, ".dl")
t = time.time()
try:
    src = hf_hub_download(repo_id=repo, filename=path, local_dir=tmp)
    shutil.move(src, os.path.join(dest, os.path.basename(path)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print(f"  ok em {time.time() - t:.0f} s")
EOF
  then
    failed=1
    log "FALHOU: $name"
  fi
done

if [ "$failed" = 1 ]; then
  log "algum download falhou (cota do volume?). Maiores arquivos:"
  find "$M" /workspace -xdev -type f -size +1G -printf '%s\t%p\n' 2>/dev/null | sort -rn | head -25 \
    | awk -F'\t' '{printf "%6.1f GB  %s\n", $1/1e9, $2}'
  exit 1
fi
log "pronto"
