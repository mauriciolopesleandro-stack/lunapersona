#!/bin/bash
# Benchmark V1.1: keepalive (o idle_shutdown nao desliga o pod), log de VRAM, pillow/numpy isolados.
cd /workspace/v11test
( while true; do curl -s -o /dev/null localhost:8000/api/generate/jobs/keepalive; sleep 45; done ) &
KA=$!
( while true; do nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader >> vram.log; sleep 5; done ) &
NV=$!
/workspace/lunapersona/.venv-persist/bin/python -m pip install -q --target pylib pillow==11.0.0 numpy==2.1.3 > pip.log 2>&1
set -a; [ -f /workspace/lunapersona/.env ] && . /workspace/lunapersona/.env; set +a
/workspace/lunapersona/.venv-persist/bin/python bench_v11.py > bench.log 2>&1
echo "EXIT $?" >> bench.log
kill $KA $NV
