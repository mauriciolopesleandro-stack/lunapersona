#!/usr/bin/env bash
# Instala a descricao automatica de fotos (Florence-2) no ComfyUI do pod.
# Usada pela foto de referencia da tela de gerar (workflows/describe-image.json).
#
# Roda uma vez por volume: o custom node e o modelo (~1,5 GB) ficam no volume.
# O node nao entra na sincronizacao entre volumes - no outro volume, rode de
# novo. Sem ele, a geracao com foto funciona igual, so sem a descricao.
set -euo pipefail

COMFY=/workspace/runpod-slim/ComfyUI
PY="$COMFY/.venv-cu128/bin/python"
NODE="$COMFY/custom_nodes/ComfyUI-Florence2"
MODEL_DIR="$COMFY/models/LLM/Florence-2-large"
LOG=/tmp/luna-logs/comfyui.log

log() { echo "[setup_florence] $*"; }
mkdir -p /tmp/luna-logs

NEW_NODE=0
if [ ! -d "$NODE" ]; then
  log "Instalando o node ComfyUI-Florence2"
  git clone -q https://github.com/kijai/ComfyUI-Florence2.git "$NODE"
  "$PY" -m pip install -q -r "$NODE/requirements.txt"
  NEW_NODE=1
fi

if [ ! -f "$MODEL_DIR/config.json" ]; then
  log "Baixando microsoft/Florence-2-large (~1,5 GB)"
  "$PY" - "$MODEL_DIR" <<'EOF'
import sys
from huggingface_hub import snapshot_download
snapshot_download(repo_id="microsoft/Florence-2-large", local_dir=sys.argv[1])
EOF
fi

if [ "$NEW_NODE" = 1 ]; then
  # O ComfyUI so carrega custom nodes ao iniciar. Reinicia com os mesmos
  # argumentos do processo atual.
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
  if curl -sf localhost:8188/object_info/Florence2Run > /dev/null; then
    log "ComfyUI de volta com o Florence-2 carregado."
  else
    log "AVISO: o ComfyUI subiu mas sem o Florence2Run - veja $LOG"
  fi
fi
log "Pronto."
