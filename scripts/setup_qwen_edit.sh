#!/usr/bin/env bash
# Instala o Qwen-Image-Edit 2511 (edicao por instrucao com varias imagens:
# "troque a mulher da imagem 1 pela da imagem 2") para o pack com a persona.
# Os nodes ja vem no ComfyUI (TextEncodeQwenImageEditPlus); aqui so os
# modelos (~31 GB): editor fp8, Lightning 8 passos (LoRA), Qwen2.5-VL fp8 e VAE.
#
# Roda uma vez por volume (so o luna-models-ro, 165 GB, tem espaco). Os
# modelos ficam fora da sincronizacao entre volumes (scripts/volume_sync.py).
set -euo pipefail

COMFY=/workspace/runpod-slim/ComfyUI
PY="$COMFY/.venv-cu128/bin/python"
M="$COMFY/models"

log() { echo "[setup_qwen_edit] $*"; }

# destino|repositorio|arquivo no repositorio
FILES=(
  "diffusion_models|Comfy-Org/Qwen-Image-Edit_ComfyUI|split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors"
  "loras|lightx2v/Qwen-Image-Edit-2511-Lightning|Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors"
  "text_encoders|Comfy-Org/Qwen-Image_ComfyUI|split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"
  "vae|Comfy-Org/Qwen-Image_ComfyUI|split_files/vae/qwen_image_vae.safetensors"
)
df -h /workspace | tail -1
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
