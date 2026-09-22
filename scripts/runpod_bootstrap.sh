#!/usr/bin/env bash
# Bootstrap do pod RunPod: liga o Ollama (LLM do chat) e o backend FastAPI
# sem precisar repetir tudo manualmente via Jupyter/terminal a cada boot.
#
# Uso (dentro do pod, depois de um git pull no repo):
#   bash scripts/runpod_bootstrap.sh
#
# E idempotente: pode rodar de novo sem duplicar processos (mata e reinicia
# o que ja estiver rodando). Le as variaveis do .env na raiz do repo.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
LOG_DIR="/tmp/luna-logs"
mkdir -p "$LOG_DIR"

echo "== 1/4: zstd (necessario pelo instalador do Ollama) =="
if ! command -v zstd >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq zstd
else
    echo "zstd ja instalado."
fi

echo "== 2/4: Ollama =="
if ! command -v ollama >/dev/null 2>&1; then
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "Ollama ja instalado."
fi

if pgrep -f "ollama serve" >/dev/null 2>&1; then
    echo "ollama serve ja rodando."
else
    nohup ollama serve > "$LOG_DIR/ollama.log" 2>&1 &
    disown
    sleep 3
    echo "ollama serve iniciado (log: $LOG_DIR/ollama.log)."
fi

LLM_MODEL="${LLM_MODEL:-llama3.2:3b}"
if ollama list | grep -q "${LLM_MODEL%%:*}"; then
    echo "Modelo $LLM_MODEL ja baixado."
else
    echo "Baixando modelo $LLM_MODEL (pode demorar)..."
    ollama pull "$LLM_MODEL"
fi

echo "== 3/4: dependencias do backend =="
pip install -q -r "$BACKEND_DIR/requirements.txt"

echo "== 4/4: backend FastAPI =="
if pgrep -f "uvicorn app.main:app" >/dev/null 2>&1; then
    echo "Backend ja rodando - reiniciando para pegar mudancas."
    pkill -f "uvicorn app.main:app" || true
    sleep 2
fi
cd "$BACKEND_DIR"
nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 > "$LOG_DIR/uvicorn.log" 2>&1 &
disown
sleep 3

echo ""
echo "=== Pronto ==="
echo "Ollama:  http://127.0.0.1:11434  (log: $LOG_DIR/ollama.log)"
echo "Backend: http://127.0.0.1:8000   (log: $LOG_DIR/uvicorn.log)"
echo "Confira com: curl http://127.0.0.1:8000/api/health"
