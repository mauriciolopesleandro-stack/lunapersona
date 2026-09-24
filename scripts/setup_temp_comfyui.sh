#!/usr/bin/env bash
# Prepara um ComfyUI "limpo" (ex: pod temporario com o template oficial de
# ComfyUI da RunPod, em qualquer regiao) com os tres arquivos que o
# workflow chroma-txt2img usa, e clona este repositorio para rodar o teste
# A/B direto no ComfyUI:
#
#   bash setup_temp_comfyui.sh            # acha o ComfyUI sozinho
#   bash setup_temp_comfyui.sh /caminho/ComfyUI
#
# Serve para quando a L4 do estudio (volume luna-models, US-MO-2) esta sem
# vaga: nao precisa do volume nem do backend. ~15 GB de download.
# Nomes dos arquivos: os mesmos de models/registry.json.
set -euo pipefail

COMFY_DIR="${1:-}"
if [ -z "$COMFY_DIR" ]; then
  for d in /workspace/ComfyUI /ComfyUI /opt/ComfyUI "$HOME/ComfyUI"; do
    if [ -f "$d/main.py" ]; then COMFY_DIR="$d"; break; fi
  done
fi
if [ -z "$COMFY_DIR" ] || [ ! -f "$COMFY_DIR/main.py" ]; then
  echo "Nao achei o ComfyUI. Passe o caminho: bash $0 /caminho/ComfyUI" >&2
  exit 1
fi
echo "ComfyUI em: $COMFY_DIR"

# Versoes antigas do ComfyUI so tem models/clip; as novas leem os dois.
TEXT_DIR="$COMFY_DIR/models/text_encoders"
if [ ! -d "$TEXT_DIR" ] && [ -d "$COMFY_DIR/models/clip" ]; then
  TEXT_DIR="$COMFY_DIR/models/clip"
fi

AUTH=()
if [ -n "${HF_TOKEN:-}" ]; then
  AUTH=(--header "Authorization: Bearer $HF_TOKEN")
fi

# fetch <arquivo> <pasta destino> <repo1> [repo2 ...]
# Procura o arquivo pela lista de arquivos de cada repo (o caminho dentro do
# repo muda entre repackages) e baixa do primeiro que tiver.
fetch() {
  local file="$1" dest_dir="$2"; shift 2
  mkdir -p "$dest_dir"
  if [ -s "$dest_dir/$file" ]; then
    echo "ok   $file (ja existe)"; return
  fi
  local repo path
  for repo in "$@"; do
    path=$(wget -qO- "${AUTH[@]}" "https://huggingface.co/api/models/$repo/tree/main?recursive=true" 2>/dev/null \
      | python3 -c "import json,sys
try: items=json.load(sys.stdin)
except Exception: items=[]
print(next((i['path'] for i in items if i.get('path','').split('/')[-1]==sys.argv[1]), ''))" "$file") || path=""
    if [ -n "$path" ]; then
      echo "baixando $file de $repo ..."
      wget -q --show-progress "${AUTH[@]}" -O "$dest_dir/$file.part" "https://huggingface.co/$repo/resolve/main/$path"
      mv "$dest_dir/$file.part" "$dest_dir/$file"
      return
    fi
  done
  echo "ERRO: nao achei $file em: $*" >&2
  echo "      (se o repo pedir aceite de licenca, rode com HF_TOKEN=... )" >&2
  exit 1
}

fetch Chroma1-HD-fp8mixed.safetensors "$COMFY_DIR/models/diffusion_models" Comfy-Org/Chroma1-HD_repackaged
fetch t5xxl_fp8_e4m3fn_scaled.safetensors "$TEXT_DIR" comfyanonymous/flux_text_encoders
fetch ae.safetensors "$COMFY_DIR/models/vae" \
  Comfy-Org/Chroma1-HD_repackaged Comfy-Org/Lumina_Image_2.0_Repackaged black-forest-labs/FLUX.1-schnell

REPO_DIR="${REPO_DIR:-/workspace/lunapersona}"
if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" pull --ff-only
else
  git clone https://github.com/mauriciolopesleandro-stack/lunapersona "$REPO_DIR"
fi

cat <<MSG

Pronto. Se o ComfyUI ja estava aberto, reinicie-o (ou so recarregue a
pagina) para ele enxergar os modelos novos. Depois rode:

  cd $REPO_DIR && python3 scripts/ab_prompt_test.py --comfyui http://127.0.0.1:8188

MSG
