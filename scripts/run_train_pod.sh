#!/usr/bin/env bash
# Roda no pod de treino da LoRA do Qwen (mesmo volume do estudio): prepara o
# dataset com o ComfyUI do volume (subido pelo /start.sh da imagem), as
# amostras do pack, desliga o ComfyUI e treina (scripts/train_qwen_lora.sh).
# O pod se desliga sozinho no fim, de qualquer jeito, e no maximo em 7 h.
set -uo pipefail
REPO=/workspace/lunapersona
LOG=/workspace/lora_qwen_train.log
PY=$REPO/.venv-persist/bin/python
log() { echo "[run_train_pod] $(date +%H:%M:%S) $*" | tee -a "$LOG"; }

( sleep 25200; log "tempo maximo atingido"; curl -s -o /dev/null -X POST -H "Authorization: Bearer $RUNPOD_API_KEY" "https://rest.runpod.io/v1/pods/$RUNPOD_POD_ID/stop" ) &

log "esperando o ComfyUI"
for i in $(seq 1 120); do curl -s -m 3 localhost:8188/system_stats > /dev/null && break; sleep 5; done

cd /workspace
log "dataset"
$PY $REPO/scripts/prep_qwen_lora_dataset.py /workspace/lora_qwen_luna >> "$LOG" 2>&1
log "amostras"
$PY $REPO/scripts/prep_qwen_lora_dataset.py /workspace/lora_qwen_luna --samples /workspace/lora_qwen_samples >> "$LOG" 2>&1
log "pares: $(ls /workspace/lora_qwen_luna/target/*.txt | wc -l), amostras: $(ls /workspace/lora_qwen_samples/c1_*.png | wc -l)"

log "desligando o ComfyUI (libera a VRAM para o treino)"
pkill -f "main.py --listen" || true
sleep 10

bash $REPO/scripts/train_qwen_lora.sh
