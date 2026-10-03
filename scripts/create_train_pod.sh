#!/usr/bin/env bash
# Cria o pod de treino da LoRA do Qwen (no mesmo volume do estudio) e desliga
# o pod do estudio (regra: um pod ligado). Roda DENTRO do pod do estudio, que
# tem RUNPOD_API_KEY e PUBLIC_KEY no ambiente. O pod de treino roda sozinho
# scripts/run_train_pod.sh (dataset, amostras, treino) e se desliga no fim.
#
# Precos na nuvem segura em EU-RO-1 (2026-10-02): RTX PRO 4500 32 GB US$ 0,72/h.
# Treino completo ~3 h (~US$ 2-3).
set -euo pipefail
envof() { tr "\0" "\n" < /proc/1/environ | sed -n "s/^$1=//p"; }
K=$(envof RUNPOD_API_KEY)
PK=$(envof PUBLIC_KEY)
SELF=$(envof RUNPOD_POD_ID)
GPU=${GPU:-"NVIDIA RTX PRO 4500 Blackwell"}

body=$(python3 - "$K" "$PK" "$GPU" <<'EOF'
import json, sys
print(json.dumps({
    "name": "luna-train-qwen",
    "imageName": "runpod/comfyui:1.3.2-comfyuiv0.30.0-cuda12.8",
    "cloudType": "SECURE", "computeType": "GPU", "gpuCount": 1,
    "gpuTypeIds": [sys.argv[3]],
    "dataCenterIds": ["EU-RO-1"], "networkVolumeId": "7s449owvmb", "volumeMountPath": "/workspace",
    "containerDiskInGb": 160, "minRAMPerGPU": 60,
    "allowedCudaVersions": ["12.8", "12.9", "13.0"],
    "ports": ["8188/http", "22/tcp"],
    "env": {"PUBLIC_KEY": sys.argv[2], "RUNPOD_API_KEY": sys.argv[1]},
    "dockerEntrypoint": ["bash", "-c",
        "(sleep 90; nohup bash /workspace/lunapersona/scripts/run_train_pod.sh > /tmp/run_train.log 2>&1 &); exec /start.sh"],
}))
EOF
)
resp=$(curl -s -X POST -H "Content-Type: application/json" -H "Authorization: Bearer $K" https://rest.runpod.io/v1/pods -d "$body")
echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id'), d.get('costPerHr'), d.get('desiredStatus')) if isinstance(d, dict) else print(d)"
if echo "$resp" | grep -q '"id"'; then
  echo "pod de treino criado; desligando o estudio ($SELF)"
  curl -s -X POST -H "Authorization: Bearer $K" "https://rest.runpod.io/v1/pods/$SELF/stop" > /dev/null
fi
