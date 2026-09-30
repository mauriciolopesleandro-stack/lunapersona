#!/usr/bin/env bash
# Instala o VoxCPM2 (TTS com portugues mais natural que o Qwen3-TTS) num venv
# proprio: as dependencias dele (gradio, datasets, funasr...) quebrariam o
# ComfyUI. Tudo no disco do container (/root): um pod novo reinstala (~5 min).
#
# Uso: bash scripts/setup_voxcpm.sh   (depois: /root/voxcpm/venv/bin/python scripts/voxcpm_tts.py jobs.json)
set -euo pipefail

ROOT=/root/voxcpm
VENV="$ROOT/venv"
MODEL="$ROOT/VoxCPM2"

log() { echo "[setup_voxcpm] $*"; }
mkdir -p "$ROOT"

if [ ! -x "$VENV/bin/python" ]; then
  log "Criando o venv"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  # torch com CUDA 12.8 (o mesmo do ComfyUI) antes do voxcpm, senao vem o de CPU.
  PIP_CONSTRAINT= "$VENV/bin/pip" install -q torch torchaudio --index-url https://download.pytorch.org/whl/cu128
  PIP_CONSTRAINT= "$VENV/bin/pip" install -q voxcpm
fi

if [ ! -f "$MODEL/config.json" ]; then
  log "Baixando openbmb/VoxCPM2"
  "$VENV/bin/python" -c "from huggingface_hub import snapshot_download; snapshot_download('openbmb/VoxCPM2', local_dir='$MODEL')"
fi

"$VENV/bin/python" -c "import torch, voxcpm; print('[setup_voxcpm] torch', torch.__version__, 'cuda', torch.cuda.is_available())"
df -h /root | tail -1
log "Pronto."
