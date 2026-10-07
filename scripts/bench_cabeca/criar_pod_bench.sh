#!/usr/bin/env bash
# Roda DENTRO do pod do estudio (que tem RUNPOD_API_KEY e PUBLIC_KEY no ambiente): cria o pod do
# benchmark "luna-bench" (RTX PRO 4500 32 GB, disco de 160 GB, mesmo volume) e DESLIGA o estudio na
# hora (regra: um pod ligado). O nome NAO comeca com luna-studio: o site nunca religa este pod.
# O pod do benchmark roda sozinho /workspace/v2test/bench/sessao_pod.sh e se desliga no fim (ou no teto).
set -euo pipefail
envof() { tr "\0" "\n" < /proc/1/environ | sed -n "s/^$1=//p"; }
K=$(envof RUNPOD_API_KEY); PK=$(envof PUBLIC_KEY); SELF=$(envof RUNPOD_POD_ID)
GPU=${GPU:-"NVIDIA RTX PRO 4500 Blackwell"}; TETO=${TETO:-75}
rm -f /workspace/v2test/bench/CONCLUIDA
body=$(python3 - "$K" "$PK" "$GPU" "$TETO" <<'EOF'
import json, sys
print(json.dumps({
    "name": "luna-bench",
    "imageName": "runpod/comfyui:1.3.2-comfyuiv0.30.0-cuda12.8",
    "cloudType": "SECURE", "computeType": "GPU", "gpuCount": 1, "gpuTypeIds": [sys.argv[3]],
    "dataCenterIds": ["EU-RO-1"], "networkVolumeId": "7s449owvmb", "volumeMountPath": "/workspace",
    "containerDiskInGb": 160, "minRAMPerGPU": 60,
    "allowedCudaVersions": ["12.8", "12.9", "13.0"],
    "ports": ["8188/http", "8189/http", "22/tcp"],
    "env": {"PUBLIC_KEY": sys.argv[2], "RUNPOD_API_KEY": sys.argv[1], "BENCH_TETO_MIN": sys.argv[4]},
    "dockerEntrypoint": ["bash", "-c", "bash /workspace/v2test/bench/sessao_pod.sh; sleep infinity"],
}))
EOF
)
resp=$(curl -s -X POST -H "Content-Type: application/json" -H "Authorization: Bearer $K" https://rest.runpod.io/v1/pods -d "$body")
echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print('BENCH', d.get('id'), d.get('costPerHr'), d.get('desiredStatus')) if isinstance(d, dict) and d.get('id') else print('ERRO', d)"
if echo "$resp" | grep -q '"id"'; then
  echo "desligando o estudio ($SELF)"
  curl -s -X POST -H "Authorization: Bearer $K" "https://rest.runpod.io/v1/pods/$SELF/stop" > /dev/null
fi
