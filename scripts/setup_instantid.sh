#!/usr/bin/env bash
# Instala o InsightFace (antelopev2: acha rostos, diz homem/mulher e compara
# rostos) e o InstantID (SDXL: redesenha o rosto com a identidade de uma foto)
# para o pack com a persona.
#
# Tudo no volume (~11,5 GB): so o luna-models-ro (150 GB) tem espaco. Os
# modelos ficam fora da sincronizacao entre volumes (scripts/volume_sync.py).
# Roda uma vez por volume; depois reinicie o ComfyUI para carregar o node.
set -euo pipefail

COMFY=/workspace/runpod-slim/ComfyUI
PY="$COMFY/.venv-cu128/bin/python"
M="$COMFY/models"
NODE="$COMFY/custom_nodes/ComfyUI_InstantID"

log() { echo "[setup_instantid] $*"; }

df -h /workspace | tail -1

# models/insightface era um link para o disco do container (setup_pulid.sh),
# que some quando o pod desliga: vira pasta de verdade no volume.
if [ -L "$M/insightface" ]; then
  rm "$M/insightface"
fi
mkdir -p "$M/insightface/models/antelopev2" "$M/instantid" "$M/controlnet" "$M/checkpoints"

if [ ! -s "$M/insightface/models/antelopev2/scrfd_10g_bnkps.onnx" ]; then
  log "Baixando antelopev2 (360 MB)"
  wget -q -O /tmp/antelopev2.zip \
    https://github.com/deepinsight/insightface/releases/download/v0.7/antelopev2.zip
  # O zip extrai numa subpasta repetida (antelopev2/antelopev2) e o
  # insightface nao acha os modelos: os .onnx vao direto para a pasta.
  "$PY" - "$M/insightface/models/antelopev2" <<'EOF'
import os, sys, zipfile
dest = sys.argv[1]
with zipfile.ZipFile("/tmp/antelopev2.zip") as z:
    for name in z.namelist():
        if name.endswith(".onnx"):
            with open(os.path.join(dest, os.path.basename(name)), "wb") as f:
                f.write(z.read(name))
EOF
  rm -f /tmp/antelopev2.zip
fi

if [ ! -d "$NODE" ]; then
  log "Instalando o node ComfyUI_InstantID"
  git clone -q https://github.com/cubiq/ComfyUI_InstantID.git "$NODE"
fi
"$PY" -c "import insightface, onnxruntime" 2>/dev/null || "$PY" -m pip install -q insightface onnxruntime
# no do estudio (rostos, homem/mulher, semelhanca com a persona): vem do git
ln -sfn /workspace/lunapersona/comfyui_nodes/luna_faces "$COMFY/custom_nodes/luna_faces"

# destino|url
FILES=(
  "instantid/instantid_ip-adapter.bin|https://huggingface.co/InstantX/InstantID/resolve/main/ip-adapter.bin"
  "controlnet/instantid_controlnet.safetensors|https://huggingface.co/InstantX/InstantID/resolve/main/ControlNetModel/diffusion_pytorch_model.safetensors"
  "checkpoints/RealVisXL_V5.0_fp16.safetensors|https://huggingface.co/SG161222/RealVisXL_V5.0/resolve/main/RealVisXL_V5.0_fp16.safetensors"
)
for entry in "${FILES[@]}"; do
  IFS="|" read -r dest url <<< "$entry"
  if [ -s "$M/$dest" ]; then
    log "ja existe: $dest"
    continue
  fi
  log "baixando $dest"
  wget -q -O "$M/$dest.part" "$url"
  mv "$M/$dest.part" "$M/$dest"
done

df -h /workspace | tail -1
log "pronto - reinicie o ComfyUI para carregar os nos do InstantID"
