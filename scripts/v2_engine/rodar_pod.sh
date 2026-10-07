#!/bin/bash
# Sessao GPU das engines V2 no pod do estudio (codigo da branch em /workspace/v2test, sem tocar no
# backend de producao). Modelos V2 no DISCO TEMPORARIO do pod (baixados do HF oficial, apagados no fim).
#   bash rodar_pod.sh <plano.json> <saida.json> <autorizado_usd> <preco_hora>
cd /workspace/v2test
PLANO=${1:-plano_smoke.json}; SAIDA=${2:-resultado_smoke.json}; AUT=${3:-0.10}; PRECO=${4:-0.57}
COMFY=/workspace/runpod-slim/ComfyUI
PY=/workspace/lunapersona/.venv-persist/bin/python
TMP=/root/v2models
LOG=sessao_v2.log
mkdir -p "$TMP" "$COMFY/models/controlnet" "$COMFY/models/instantid" "$COMFY/models/checkpoints"
echo "inicio $(date +%s)" > $LOG
# destino no ComfyUI | url. Arquivo REAL ja no volume = usa o do volume (nao baixa,
# nao substitui, nao apaga). So o que este script linkou sai no fim (linkados.txt).
MODELS=(
  "checkpoints/RealVisXL_V5.0_fp16.safetensors|https://huggingface.co/SG161222/RealVisXL_V5.0/resolve/main/RealVisXL_V5.0_fp16.safetensors"
  "controlnet/controlnet-union-sdxl-1.0-promax.safetensors|https://huggingface.co/xinsir/controlnet-union-sdxl-1.0/resolve/main/diffusion_pytorch_model_promax.safetensors"
  "instantid/instantid_ip-adapter.bin|https://huggingface.co/InstantX/InstantID/resolve/main/ip-adapter.bin"
  "controlnet/instantid_controlnet.safetensors|https://huggingface.co/InstantX/InstantID/resolve/main/ControlNetModel/diffusion_pytorch_model.safetensors"
)
: > linkados.txt
PIDS=()
for e in "${MODELS[@]}"; do
  IFS="|" read -r dest url <<< "$e"; f=$(basename "$dest")
  if [ -e "$COMFY/models/$dest" ] && [ ! -L "$COMFY/models/$dest" ]; then echo "volume $dest" >> $LOG; continue; fi
  ( [ -s "$TMP/$f" ] || { curl -sL --fail -o "$TMP/$f.part" "$url" && mv "$TMP/$f.part" "$TMP/$f"; } ) &
  PIDS+=($!); echo "$dest" >> linkados.txt
done
[ -d pylib/PIL ] || $PY -m pip install -q --target pylib pillow==11.0.0 numpy==2.1.3 > pip.log 2>&1
rm -rf personas_run && mkdir -p personas_run/luna
cp /workspace/lunapersona/personas/luna/persona.json personas_run/luna/
ln -s /workspace/lunapersona/personas/luna/references personas_run/luna/references
cp personas/luna/persona_sheet.json personas_run/luna/
( while true; do curl -s -o /dev/null localhost:8000/api/generate/jobs/keepalive; sleep 45; done ) &
KA=$!
( while true; do echo "$(date +%s),$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)" >> vram_v2.log; sleep 2; done ) &
NV=$!
wait "${PIDS[@]}"
echo "download $(date +%s) $(stat -c '%n=%s' $TMP/* | tr '\n' ' ')" >> $LOG
echo "sha $(cd $TMP && sha256sum * | cut -c1-16 | tr '\n' ' ')" >> $LOG
while read -r dest; do ln -sfn "$TMP/$(basename "$dest")" "$COMFY/models/$dest"; done < linkados.txt
until curl -s -o /dev/null 127.0.0.1:8188/queue; do sleep 2; done
curl -s -X POST 127.0.0.1:8188/api/refresh > /dev/null 2>&1
echo "comfy $(date +%s) $(curl -s 127.0.0.1:8188/system_stats | head -c 400)" >> $LOG
set -a; [ -f /workspace/lunapersona/.env ] && . /workspace/lunapersona/.env; set +a
V2_ROOT=/workspace/v2test V2_PRECO_HORA=$PRECO $PY sessao_v2.py --plano "$PLANO" --saida "$SAIDA" --autorizado "$AUT" >> $LOG 2>&1
echo "EXIT $? $(date +%s)" >> $LOG
$PY coletar_v2.py "$SAIDA" saida_v2 >> $LOG 2>&1
while read -r dest; do [ -L "$COMFY/models/$dest" ] && rm -f "$COMFY/models/$dest"; done < linkados.txt
rm -f "$TMP"/*
kill $KA $NV
echo FIM >> $LOG
