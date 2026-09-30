#!/usr/bin/env bash
# Instala o video da persona: Wan 2.2 Image-to-Video 14B (fp8) + LoRAs
# LightX2V de 4 passos, nos modelos do ComfyUI do pod (~38 GB).
# Usado por workflows/wan22-i2v.json (botao "Animar" do site).
#
# Roda uma vez por volume. Os arquivos ficam fora da sincronizacao entre
# volumes (scripts/volume_sync.py): so o luna-models-ro (90 GB) tem espaco.
set -euo pipefail

COMFY=/workspace/runpod-slim/ComfyUI
PY="$COMFY/.venv-cu128/bin/python"
M="$COMFY/models"

log() { echo "[setup_wan] $*"; }

# destino|repositorio|arquivo no repositorio
FILES=(
  "diffusion_models|Comfy-Org/Wan_2.2_ComfyUI_Repackaged|split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
  "diffusion_models|Comfy-Org/Wan_2.2_ComfyUI_Repackaged|split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
  "loras|Comfy-Org/Wan_2.2_ComfyUI_Repackaged|split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
  "loras|Comfy-Org/Wan_2.2_ComfyUI_Repackaged|split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"
  "text_encoders|Comfy-Org/Wan_2.1_ComfyUI_repackaged|split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
  "vae|Comfy-Org/Wan_2.1_ComfyUI_repackaged|split_files/vae/wan_2.1_vae.safetensors"
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

if curl -sf localhost:8188/object_info/WanImageToVideo > /dev/null; then
  log "ComfyUI tem o no WanImageToVideo."
else
  log "AVISO: o ComfyUI nao tem WanImageToVideo - atualize o ComfyUI."
fi
log "Pronto."
