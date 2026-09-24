#!/usr/bin/env bash
# Autostart dos pods do estudio criados por /api/runpod-wake (ver
# DOCKER_ENTRYPOINT em frontend/api/_runpod.ts): a imagem baixa este script
# do GitHub e roda em segundo plano, antes do /start.sh normal dela (SSH,
# Jupyter, ComfyUI). Log em /tmp/luna-autostart.log.
#
#   1. garante o repo em /workspace/lunapersona (clona se o volume for novo);
#   2. sincroniza config/personas com o outro volume (antes do backend, que
#      le o .env);
#   3. sobe Ollama + backend (scripts/runpod_bootstrap.sh);
#   4. sincroniza os modelos e segue sincronizando config/personas a cada
#      poucos minutos enquanto o pod estiver ligado.
set -uo pipefail

REPO_URL="https://github.com/mauriciolopesleandro-stack/lunapersona.git"
REPO_DIR="/workspace/lunapersona"
LOG_DIR="/tmp/luna-logs"
SYNC_VENV="$REPO_DIR/.venv-sync"
SYNC_INTERVAL_S=300
mkdir -p "$LOG_DIR"

echo "== autostart $(date -u +%FT%TZ) pod=${RUNPOD_POD_ID:-?} volume=${LUNA_SELF_VOLUME_ID:-?} (${LUNA_SELF_DATACENTER:-?}) =="

for _ in $(seq 1 60); do
    [ -d /workspace ] && break
    sleep 2
done

FIRST_SYNC_FLAGS=""
if [ -d "$REPO_DIR/.git" ]; then
    git -C "$REPO_DIR" pull --ff-only || echo "AVISO: git pull falhou - seguindo com o codigo que ja esta no volume."
else
    echo "Volume sem o repositorio - clonando."
    git clone "$REPO_URL" "$REPO_DIR"
    # Tudo acabou de ser criado pelo clone: nas diferencas, vale o outro volume
    # (mesmo se este for o original - o clone nao tem nada editado).
    FIRST_SYNC_FLAGS="--prefer-remote"
fi

if [ ! -x "$SYNC_VENV/bin/python" ]; then
    python3 -m venv "$SYNC_VENV"
fi
"$SYNC_VENV/bin/pip" install -q -r "$REPO_DIR/scripts/requirements-sync.txt" || echo "AVISO: nao instalou boto3 - sincronizacao desligada."

"$SYNC_VENV/bin/python" "$REPO_DIR/scripts/volume_sync.py" small $FIRST_SYNC_FLAGS

# Volume-copia que ainda nao recebeu tudo (sem marcador de pronto): puxa
# config + modelos do original ANTES de subir o backend - o frontend so
# entra quando o backend responde, entao o estudio nunca abre vazio.
if [ "${LUNA_SELF_ORIGINAL:-0}" != "1" ] && [ ! -f /workspace/.luna-sync/ready.json ]; then
    echo "Volume-copia incompleto - copiando tudo do volume original antes de subir o backend."
    for _ in $(seq 1 300); do
        [ -f /workspace/runpod-slim/ComfyUI/main.py ] && break
        sleep 2
    done
    "$SYNC_VENV/bin/python" "$REPO_DIR/scripts/volume_sync.py" all \
        || echo "AVISO: a copia inicial teve problemas - subindo o backend mesmo assim."
fi

bash "$REPO_DIR/scripts/runpod_bootstrap.sh" > "$LOG_DIR/bootstrap.log" 2>&1 \
    || echo "AVISO: bootstrap saiu com erro (ver $LOG_DIR/bootstrap.log)."

# Num volume novo, o /start.sh da imagem so copia o ComfyUI para
# /workspace/runpod-slim/ComfyUI se a pasta ainda NAO existir - baixar os
# modelos antes criaria a pasta e a instalacao seria pulada.
for _ in $(seq 1 300); do
    [ -f /workspace/runpod-slim/ComfyUI/main.py ] && break
    sleep 2
done

# "all" (config + modelos): quando termina sem erro, marca os dois volumes
# como iguais - so entao a Vercel passa a usar o volume-copia.
"$SYNC_VENV/bin/python" "$REPO_DIR/scripts/volume_sync.py" all

while true; do
    sleep "$SYNC_INTERVAL_S"
    "$SYNC_VENV/bin/python" "$REPO_DIR/scripts/volume_sync.py" small
done
