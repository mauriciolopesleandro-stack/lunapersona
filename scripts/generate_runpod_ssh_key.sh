#!/usr/bin/env bash
# Gera (ou regenera) o par de chaves SSH usado pela automacao de bootstrap
# do pod (frontend/api/runpod-bootstrap.ts). Roda local, nunca no pod.
#
# Depois de rodar:
#   1. Cole o CONTEUDO de .secrets/runpod_ssh_key (a chave PRIVADA) na
#      variavel RUNPOD_SSH_PRIVATE_KEY, direto no painel da Vercel
#      (Project Settings > Environment Variables) - nunca num arquivo do Git.
#   2. Cole o CONTEUDO de .secrets/runpod_ssh_key.pub (a chave PUBLICA, essa
#      nao e segredo) na env var PUBLIC_KEY do pod, no RunPod
#      (Edit Pod > Environment variables).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$REPO_ROOT/.secrets"
mkdir -p "$OUT_DIR"

ssh-keygen -t ed25519 -f "$OUT_DIR/runpod_ssh_key" -N "" -C "luna-runpod-wake"

echo ""
echo "Chaves geradas em $OUT_DIR/ (fora do Git - ver .gitignore)."
echo "Privada -> RUNPOD_SSH_PRIVATE_KEY na Vercel."
echo "Publica  -> PUBLIC_KEY no pod (RunPod > Edit Pod > Environment variables)."
