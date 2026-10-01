#!/usr/bin/env bash
# Instala a troca de personagem em video (Wan 2.2 Animate, modo substituir):
# custom nodes de pose (comfyui_controlnet_aux: DWPose) e recorte de pessoa
# (ComfyUI-segment-anything-2: SAM2) + modelos (~22 GB). Usado por
# backend/app/services/swap_service.py (botao "Trocar personagem" do site).
#
# Roda uma vez por volume (so o luna-models-ro, 130 GB, tem espaco). Os
# modelos ficam fora da sincronizacao entre volumes (scripts/volume_sync.py).
set -euo pipefail

COMFY=/workspace/runpod-slim/ComfyUI
PY="$COMFY/.venv-cu128/bin/python"
NODES="$COMFY/custom_nodes"
M="$COMFY/models"
LOG=/tmp/luna-logs/comfyui.log

log() { echo "[setup_animate] $*"; }
mkdir -p /tmp/luna-logs

NEW_NODE=0
install_node() {  # pasta repo
  if [ ! -d "$NODES/$1" ]; then
    log "Instalando o node $1"
    git clone -q "$2" "$NODES/$1"
    if [ -f "$NODES/$1/requirements.txt" ]; then
      # Sem torch/torchvision/torchaudio: reinstalar trocaria a versao CUDA do ComfyUI.
      grep -viE '^(torch|torchvision|torchaudio)([<>=~ ]|$)' "$NODES/$1/requirements.txt" > "/tmp/req-$1.txt" || true
      "$PY" -m pip install -q -r "/tmp/req-$1.txt"
    fi
    NEW_NODE=1
  fi
}
install_node comfyui_controlnet_aux https://github.com/Fannovel16/comfyui_controlnet_aux.git
install_node ComfyUI-segment-anything-2 https://github.com/kijai/ComfyUI-segment-anything-2.git

# destino|repositorio|arquivo no repositorio
FILES=(
  "diffusion_models|Kijai/WanVideo_comfy_fp8_scaled|Wan22Animate/Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors"
  "loras|Kijai/WanVideo_comfy|LoRAs/Wan22_relight/WanAnimate_relight_lora_fp16.safetensors"
  "loras|Kijai/WanVideo_comfy|Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"
  "clip_vision|Comfy-Org/Wan_2.1_ComfyUI_repackaged|split_files/clip_vision/clip_vision_h.safetensors"
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

if [ "$NEW_NODE" = 1 ]; then
  # O ComfyUI so carrega custom nodes ao iniciar: reinicia com os mesmos argumentos.
  PID="$(pgrep -f "main.py --listen" | head -1 || true)"
  ARGS="--listen 0.0.0.0 --port 8188 --enable-cors-header --disable-cuda-malloc"
  if [ -n "$PID" ]; then
    ARGS="$(tr '\0' ' ' < "/proc/$PID/cmdline" | sed 's/^.*main\.py //')"
    log "Reiniciando o ComfyUI (pid $PID)"
    kill "$PID"
    for _ in $(seq 1 30); do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
  fi
  cd "$COMFY"
  # shellcheck disable=SC2086
  nohup "$PY" main.py $ARGS > "$LOG" 2>&1 &
  for _ in $(seq 1 120); do
    curl -sf localhost:8188/system_stats > /dev/null && break
    sleep 2
  done
fi

for n in DWPreprocessor DownloadAndLoadSAM2Model Sam2Segmentation Florence2toCoordinates WanAnimateToVideo; do
  if curl -sf "localhost:8188/object_info/$n" | grep -q "\"$n\""; then
    log "ok: $n"
  else
    log "AVISO: o ComfyUI nao tem $n - veja $LOG"
  fi
done
df -h /workspace | tail -1
log "Pronto."
