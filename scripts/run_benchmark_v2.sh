#!/bin/bash
# Persona Engine V2, teste 1 (base). Roda no pod em /workspace/v2test.
# - keepalive (o idle_shutdown nao desliga o pod no meio) e log de VRAM;
# - cada checkpoint e baixado no DISCO TEMPORARIO do pod (/root/v2models, o volume
#   esta cheio), ligado por link em models/checkpoints, usado e APAGADO em seguida;
# - Lustify precisa do token do Civitai em /root/.civitai_token (digitado pelo usuario).
# Uso: bash run_benchmark_v2.sh realvisxl lustify
cd /workspace/v2test
COMFY=/workspace/runpod-slim/ComfyUI
PY=/workspace/lunapersona/.venv-persist/bin/python
TMP=/root/v2models
mkdir -p "$TMP"
# persona: persona.json e masters do VOLUME + a ficha da branch (o sha256 das masters e conferido no script)
rm -rf personas_run && mkdir -p personas_run/luna
cp /workspace/lunapersona/personas/luna/persona.json personas_run/luna/
ln -s /workspace/lunapersona/personas/luna/references personas_run/luna/references
cp personas/luna/persona_sheet.json personas_run/luna/
( while true; do curl -s -o /dev/null localhost:8000/api/generate/jobs/keepalive; sleep 45; done ) &
KA=$!
( while true; do nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader >> vram.log; sleep 5; done ) &
NV=$!
$PY -m pip install -q --target pylib pillow==11.0.0 numpy==2.1.3 > pip.log 2>&1
df -h /root | tail -1 >> bench.log

for MODEL in "$@"; do
  read -r FILE URL SIZE SHA <<< "$($PY -c "
import json; m = json.load(open('config/persona_engine_v2.json'))['models']['$MODEL']
print(m['checkpoint'], m['source'], m['size_bytes'], m['sha256'])")"
  AUTH=()
  if [ "$MODEL" = "lustify" ]; then
    [ -s /root/.civitai_token ] || { echo "SEM TOKEN CIVITAI - lustify pulado" >> bench.log; continue; }
    AUTH=(-H "Authorization: Bearer $(cat /root/.civitai_token)")
  fi
  T0=$(date +%s)
  curl -sL --fail "${AUTH[@]}" -o "$TMP/$FILE" "$URL" || { echo "DOWNLOAD FALHOU $MODEL" >> bench.log; continue; }
  echo "$MODEL download $(( $(date +%s) - T0 )) s, $(stat -c %s "$TMP/$FILE") bytes (esperado $SIZE)" >> bench.log
  ( echo "$MODEL sha256 $(sha256sum "$TMP/$FILE" | cut -d' ' -f1) (esperado $SHA)" >> bench.log ) &
  SHAPID=$!
  ln -sf "$TMP/$FILE" "$COMFY/models/checkpoints/$FILE"
  $PY benchmark_v2_base.py "$MODEL" >> bench.log 2>&1
  echo "$MODEL EXIT $?" >> bench.log
  wait $SHAPID
  rm -f "$COMFY/models/checkpoints/$FILE" "$TMP/$FILE"
done
rm -f /root/.civitai_token
echo FIM >> bench.log
kill $KA $NV
