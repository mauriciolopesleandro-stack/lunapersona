#!/usr/bin/env bash
# Instala a voz da persona: custom node ComfyUI-Qwen-TTS + modelos Qwen3-TTS
# 1.7B VoiceDesign (criar a voz) e 1.7B Base (clonar a voz escolhida), ~10 GB.
# Usado por workflows/voice-design.json e voice-clone.json (aba Voz do site).
#
# Roda uma vez por volume. Nem o node nem os modelos (models/qwen-tts) entram
# na sincronizacao entre volumes: so o luna-models-ro tem espaco.
set -euo pipefail

COMFY=/workspace/runpod-slim/ComfyUI
PY="$COMFY/.venv-cu128/bin/python"
NODE="$COMFY/custom_nodes/ComfyUI-Qwen-TTS"
MODELS="$COMFY/models/qwen-tts"
LOG=/tmp/luna-logs/comfyui.log

log() { echo "[setup_voice] $*"; }
mkdir -p /tmp/luna-logs "$MODELS"

"$PY" -c "import torch, transformers; print('[setup_voice] antes: torch', torch.__version__, 'transformers', transformers.__version__)"

NEW_NODE=0
if [ ! -d "$NODE" ]; then
  log "Instalando o node ComfyUI-Qwen-TTS"
  git clone -q https://github.com/flybirdxx/ComfyUI-Qwen-TTS.git "$NODE"
  # Sem torch/torchaudio (ja vem com o ComfyUI; reinstalar trocaria a versao
  # CUDA) e sem onnxruntime-openvino (so para CPU Intel).
  grep -viE '^(torch|torchaudio|onnxruntime)' "$NODE/requirements.txt" > /tmp/qwen-tts-req.txt
  "$PY" -m pip install -q -r /tmp/qwen-tts-req.txt
  "$PY" -c "import torchaudio" 2>/dev/null || "$PY" -m pip install -q torchaudio --index-url https://download.pytorch.org/whl/cu128
  NEW_NODE=1
fi

"$PY" - "$MODELS" <<'EOF'
import os, sys
from huggingface_hub import snapshot_download
root = sys.argv[1]
for repo in ("Qwen/Qwen3-TTS-Tokenizer-12Hz", "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign", "Qwen/Qwen3-TTS-12Hz-1.7B-Base"):
    target = os.path.join(root, repo.split("/")[-1])
    if os.path.isfile(os.path.join(target, "config.json")):
        print(f"[setup_voice] ja existe: {repo}")
        continue
    print(f"[setup_voice] baixando {repo}")
    snapshot_download(repo_id=repo, local_dir=target)
EOF

"$PY" -c "import torch, transformers; print('[setup_voice] depois: torch', torch.__version__, 'transformers', transformers.__version__)"

if [ "$NEW_NODE" = 1 ]; then
  # O ComfyUI so carrega custom nodes ao iniciar: reinicia com os mesmos argumentos.
  PID="$(pgrep -f "main.py --listen" | head -1 || true)"
  ARGS="--listen 0.0.0.0 --port 8188 --enable-cors-header"
  if [ -n "$PID" ]; then
    ARGS="$(tr '\0' ' ' < "/proc/$PID/cmdline" | sed 's/^.*main\.py //')"
    log "Reiniciando o ComfyUI (pid $PID)"
    kill "$PID"
    for _ in $(seq 1 30); do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
  fi
  cd "$COMFY"
  # shellcheck disable=SC2086
  nohup "$PY" main.py $ARGS > "$LOG" 2>&1 &
  for _ in $(seq 1 90); do
    curl -sf localhost:8188/system_stats > /dev/null && break
    sleep 2
  done
fi

for n in FB_Qwen3TTSVoiceDesign FB_Qwen3TTSVoiceClone SaveAudioMP3 Florence2Run WanImageToVideo; do
  if curl -sf "localhost:8188/object_info/$n" | grep -q "\"$n\""; then
    log "ok: $n"
  else
    log "AVISO: o ComfyUI nao tem $n - veja $LOG"
  fi
done
log "Pronto."
