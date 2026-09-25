#!/usr/bin/env bash
# Prepara o PuLID-Flux-Chroma (identidade por foto de referencia) no ComfyUI do pod.
#
# Os modelos (~2,5 GB) ficam no disco do container (/root/pulid), nao no volume:
# o volume de rede esta no limite de 40 GB. Por isso eles somem quando o pod
# desliga e este script precisa rodar de novo a cada pod ligado. O node e as
# dependencias Python ficam no volume e so sao instalados na primeira vez.
set -euo pipefail

COMFY=/workspace/runpod-slim/ComfyUI
PY="$COMFY/.venv-cu128/bin/python"
NODE="$COMFY/custom_nodes/ComfyUI-PuLID-Flux-Chroma"
CACHE=/root/pulid
SITE="$("$PY" -c 'import site; print(site.getsitepackages()[0])')"
NEEDS_RESTART=0

log() { echo "[setup_pulid] $*"; }

mkdir -p "$CACHE/insightface/models/antelopev2" "$CACHE/facexlib" "$COMFY/models/pulid"

if [ ! -d "$NODE" ]; then
  log "Instalando o node PuLID-Flux-Chroma"
  git clone -q https://github.com/PaoloC68/ComfyUI-PuLID-Flux-Chroma.git "$NODE"
  NEEDS_RESTART=1
fi
if ! "$PY" -c "import insightface, facexlib, timm, ftfy, onnxruntime" 2>/dev/null; then
  log "Instalando dependencias Python do node"
  "$PY" -m pip install -q -r "$NODE/requirements.txt"
  NEEDS_RESTART=1
fi

if [ ! -s "$CACHE/pulid_flux_v0.9.1.safetensors" ]; then
  log "Baixando pulid_flux_v0.9.1.safetensors (1,1 GB)"
  wget -q -O "$CACHE/pulid_flux_v0.9.1.safetensors.part" \
    https://huggingface.co/guozinan/PuLID/resolve/main/pulid_flux_v0.9.1.safetensors
  mv "$CACHE/pulid_flux_v0.9.1.safetensors.part" "$CACHE/pulid_flux_v0.9.1.safetensors"
fi

# O zip oficial do antelopev2 extrai numa subpasta repetida (antelopev2/antelopev2),
# e o insightface nao acha os modelos - por isso os .onnx sao extraidos direto.
if [ ! -s "$CACHE/insightface/models/antelopev2/scrfd_10g_bnkps.onnx" ]; then
  log "Baixando antelopev2 (360 MB)"
  wget -q -O /tmp/antelopev2.zip \
    https://github.com/deepinsight/insightface/releases/download/v0.7/antelopev2.zip
  "$PY" - "$CACHE/insightface/models/antelopev2" <<'EOF'
import os, sys, zipfile
dest = sys.argv[1]
with zipfile.ZipFile("/tmp/antelopev2.zip") as z:
    for name in z.namelist():
        if name.endswith(".onnx"):
            with open(os.path.join(dest, os.path.basename(name)), "wb") as f:
                f.write(z.read(name))
EOF
  rm -f /tmp/antelopev2.zip
fi

# Aponta para o disco do container as pastas onde o node le ou baixa modelos.
# Uma pasta de verdade que ja exista e movida para o lado, nunca apagada.
link_dir() {
  local target="$1" dest="$2"
  if [ -e "$target" ] && [ ! -L "$target" ]; then
    mv "$target" "$dest.old.$(date +%s)"
  fi
  ln -sfn "$dest" "$target"
}
ln -sfn "$CACHE/pulid_flux_v0.9.1.safetensors" "$COMFY/models/pulid/pulid_flux_v0.9.1.safetensors"
link_dir "$COMFY/models/insightface" "$CACHE/insightface"
link_dir "$SITE/facexlib/weights" "$CACHE/facexlib"

log "Pronto. Modelos em $CACHE ($(du -sh "$CACHE" | cut -f1))."
if [ "$NEEDS_RESTART" = 1 ]; then
  log "Node ou dependencias instalados agora: reinicie o ComfyUI para carregar os nos do PuLID."
fi
