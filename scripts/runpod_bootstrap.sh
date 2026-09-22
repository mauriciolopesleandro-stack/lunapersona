#!/usr/bin/env bash
# Bootstrap do pod RunPod: liga o Ollama (LLM do chat) e o backend FastAPI
# sem precisar repetir tudo manualmente via Jupyter/terminal a cada boot.
#
# Uso (dentro do pod, depois de um git pull no repo):
#   bash scripts/runpod_bootstrap.sh
#
# E idempotente: pode rodar de novo sem duplicar processos (mata e reinicia
# o que ja estiver rodando). Le as variaveis do .env na raiz do repo.
#
# IMPORTANTE (economia): o modelo do Ollama e o venv Python ficam guardados
# dentro do proprio repo (REPO_ROOT), que vive no Network Volume persistente
# (/workspace) - ou seja, sobrevivem a pods novos/migracoes. So baixam/
# instalam de verdade na PRIMEIRA vez; nos boots seguintes so reiniciam os
# processos, o que economiza minutos (e dinheiro) de GPU ligada a toa.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
LOG_DIR="/tmp/luna-logs"
VENV_DIR="$REPO_ROOT/.venv-persist"
export OLLAMA_MODELS="$REPO_ROOT/.ollama-models"
mkdir -p "$LOG_DIR" "$OLLAMA_MODELS"

echo "== 1/5: zstd (necessario pelo instalador do Ollama) =="
if ! command -v zstd >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq zstd
else
    echo "zstd ja instalado."
fi

echo "== 2/5: Ollama (binario) =="
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
    echo "ollama serve iniciado (log: $LOG_DIR/ollama.log). Modelos em: $OLLAMA_MODELS"
fi

echo "== 3/5: modelo do chat =="
LLM_MODEL="${LLM_MODEL:-llama3.2:3b}"
if ollama list | grep -q "${LLM_MODEL%%:*}"; then
    echo "Modelo $LLM_MODEL ja esta em $OLLAMA_MODELS (nao precisa baixar de novo)."
else
    echo "Baixando modelo $LLM_MODEL pela primeira vez (fica salvo no volume)..."
    ollama pull "$LLM_MODEL"
fi

echo "== 4/5: dependencias do backend (venv persistente) =="
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "Criando venv em $VENV_DIR (so acontece uma vez)..."
    python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install -q -r "$BACKEND_DIR/requirements.txt"

echo "== 5/5: backend FastAPI =="
if pgrep -f "uvicorn app.main:app" >/dev/null 2>&1; then
    echo "Backend ja rodando - reiniciando para pegar mudancas."
    pkill -f "uvicorn app.main:app" || true
    sleep 2
fi
cd "$BACKEND_DIR"
# Sobrescreve por variavel de ambiente (tem prioridade sobre o .env no
# pydantic-settings) para o caso do .env do pod ter uma linha LLM_API_URL=
# vazia (sobra de antes dessa feature existir) - evita reeditar o .env.
export LLM_API_URL="${LLM_API_URL:-http://127.0.0.1:11434}"
nohup "$VENV_DIR/bin/uvicorn" app.main:app --host 0.0.0.0 --port 8000 > "$LOG_DIR/uvicorn.log" 2>&1 &
disown
sleep 3

echo ""
echo "=== Pronto ==="
echo "Ollama:  http://127.0.0.1:11434  (log: $LOG_DIR/ollama.log)"
echo "Backend: http://127.0.0.1:8000   (log: $LOG_DIR/uvicorn.log)"
echo "Confira com: curl http://127.0.0.1:8000/api/health"
