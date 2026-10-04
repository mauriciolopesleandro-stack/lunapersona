#!/usr/bin/env bash
# Modelos do caminho rapido (pesquisa de mercado, 2026-10):
#  - Z-Image Turbo (Tongyi/Alibaba, Apache 2.0): cena em 8 passos, segundos por
#    foto e pele mais real que o Chroma. ~18 GB (DiT bf16 + Qwen3-4B fp8); o VAE
#    e o ae.safetensors do FLUX que ja esta no volume.
#  - BFS Best Face Swap head V5 para o Qwen-Image-Edit 2511 (MIT, ~0.3 GB): troca
#    a cabeca inteira (rosto + cabelo) numa passada, com a luz da cena. Usa o
#    Qwen-Image-Edit 2511 + Lightning ja instalados por scripts/setup_qwen_edit.sh.
#
# Roda uma vez no luna-models-ro. Nao apaga nada: se faltar espaco, mostra os
# maiores arquivos para decidir o que tirar.
set -euo pipefail

COMFY=/workspace/runpod-slim/ComfyUI
PY="$COMFY/.venv-cu128/bin/python"
M="$COMFY/models"
NEED_GB=19

log() { echo "[setup_fast] $*"; }

# destino|repositorio|arquivo no repositorio
FILES=(
  "diffusion_models|Comfy-Org/z_image_turbo|split_files/diffusion_models/z_image_turbo_bf16.safetensors"
  "text_encoders|Comfy-Org/z_image_turbo|split_files/text_encoders/qwen_3_4b_fp8_mixed.safetensors"
  "loras|Alissonerdx/BFS-Best-Face-Swap|bfs_head_v5_2511_merged_version_rank_16_fp16.safetensors"
)

df -h /workspace | tail -1
free_gb=$(df -BG --output=avail /workspace | tail -1 | tr -dc '0-9')
missing=0
for entry in "${FILES[@]}"; do
  IFS="|" read -r dest repo path <<< "$entry"
  [ -f "$M/$dest/$(basename "$path")" ] || missing=1
done
if [ "$missing" = 1 ] && [ "${free_gb:-0}" -lt "$NEED_GB" ]; then
  log "so ${free_gb} GB livres, precisa de ~${NEED_GB} GB. Maiores arquivos de modelo:"
  find "$M" /workspace -xdev -type f -size +2G -printf '%s\t%p\n' 2>/dev/null | sort -rn | head -25 \
    | awk -F'\t' '{printf "%6.1f GB  %s\n", $1/1e9, $2}'
  exit 1
fi
[ -f "$M/vae/ae.safetensors" ] || log "AVISO: falta models/vae/ae.safetensors (VAE do FLUX)"

for entry in "${FILES[@]}"; do
  IFS="|" read -r dest repo path <<< "$entry"
  name="$(basename "$path")"
  mkdir -p "$M/$dest"
  if [ -f "$M/$dest/$name" ]; then
    log "ja existe: $dest/$name"
    continue
  fi
  log "baixando $name"
  "$PY" - "$repo" "$path" "$M/$dest" <<'EOF'
import os, shutil, sys
from huggingface_hub import hf_hub_download
repo, path, dest = sys.argv[1:4]
tmp = os.path.join(dest, ".dl")
src = hf_hub_download(repo_id=repo, filename=path, local_dir=tmp)
shutil.move(src, os.path.join(dest, os.path.basename(path)))
shutil.rmtree(tmp, ignore_errors=True)
EOF
done
df -h /workspace | tail -1
log "pronto"
