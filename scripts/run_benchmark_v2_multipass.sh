#!/bin/bash
# V2 teste 2 (multi-pass RealVisXL). Roda no pod em /workspace/v2test.
# RealVisXL no disco temporario do pod (apagado no fim); LoRA SDXL da Luna ja no volume.
cd /workspace/v2test
COMFY=/workspace/runpod-slim/ComfyUI
PY=/workspace/lunapersona/.venv-persist/bin/python
TMP=/root/v2models
FILE=RealVisXL_V5.0_fp16.safetensors
mkdir -p "$TMP"
echo "inicio $(date +%s)" > bench.log
curl -sL --fail -o "$TMP/$FILE.part" "https://huggingface.co/SG161222/RealVisXL_V5.0/resolve/main/$FILE" && mv "$TMP/$FILE.part" "$TMP/$FILE" &
DL=$!
$PY -m pip install -q --target pylib pillow==11.0.0 numpy==2.1.3 > pip.log 2>&1 &
PIP=$!
rm -rf personas_run && mkdir -p personas_run/luna
cp /workspace/lunapersona/personas/luna/persona.json personas_run/luna/
ln -s /workspace/lunapersona/personas/luna/references personas_run/luna/references
cp personas/luna/persona_sheet.json personas_run/luna/
( while true; do curl -s -o /dev/null localhost:8000/api/generate/jobs/keepalive; sleep 45; done ) &
KA=$!
( while true; do nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader >> vram.log; sleep 3; done ) &
NV=$!
wait $DL; echo "download $(date +%s) $(stat -c %s "$TMP/$FILE" 2>/dev/null)" >> bench.log
ln -sf "$TMP/$FILE" "$COMFY/models/checkpoints/$FILE"
ls -la "$COMFY/models/loras/lunavox_sdxl_v1.safetensors" >> bench.log 2>&1
wait $PIP
until curl -s -o /dev/null 127.0.0.1:8188/queue; do sleep 2; done
echo "comfy $(date +%s)" >> bench.log
$PY benchmark_v2_multipass.py --teto 0.10 --overhead 180 >> bench.log 2>&1
echo "EXIT $? $(date +%s)" >> bench.log
rm -f "$COMFY/models/checkpoints/$FILE" "$TMP/$FILE"
kill $KA $NV
echo FIM >> bench.log
