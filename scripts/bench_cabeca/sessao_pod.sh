#!/bin/bash
# Sessao UNICA do benchmark de troca de cabeca no pod luna-bench (RTX PRO 4500 32 GB, disco 160 GB).
# Roda sozinho no boot do pod (dockerEntrypoint). Nao toca no ComfyUI de producao (volume): monta um
# ComfyUI v0.37 SEPARADO no disco do pod (porta 8189), que le os modelos do volume + os baixados aqui.
# Desliga o pod: quando a sessao local manda /fim, ou pelo TETO (padrao 75 min), ou em qualquer erro.
set -u
B=/root/bench
V=/workspace/v2test/bench
mkdir -p $B/models/diffusion_models $B/models/loras $B/models/text_encoders $B/models/vae
exec >> $B/sessao.log 2>&1
envof() { tr "\0" "\n" < /proc/1/environ | sed -n "s/^$1=//p"; }
KEY=$(envof RUNPOD_API_KEY); POD=$(envof RUNPOD_POD_ID); TETO=$(envof BENCH_TETO_MIN); TETO=${TETO:-75}
log() { echo "[$(date +%H:%M:%S)] $*"; }
desligar() {
  log "DESLIGANDO ($1)"; cp $B/*.log $B/*.csv $V/ 2>/dev/null
  curl -s -X POST -H "Authorization: Bearer $KEY" "https://rest.runpod.io/v1/pods/$POD/stop" > /dev/null
  sleep 60; exit 0
}
trap 'desligar "erro no script"' ERR
if [ -f $V/CONCLUIDA ]; then log "sessao ja concluida antes: nao roda de novo"; desligar "ja concluida"; fi
( sleep $((TETO * 60)); log "TETO de $TETO min"; touch $B/fim_teto ) &
nohup python3 $V/servidor_status.py > $B/status.log 2>&1 &
log "pod $POD | teto $TETO min"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
df -h /root /workspace | tail -2
LIVRE=$(df --output=avail -BG /root | tail -1 | tr -dc 0-9)
if [ "$LIVRE" -lt 120 ]; then echo "disco $LIVRE GB < 120" > $B/falhou_DISCO; desligar "disco insuficiente"; fi
( while true; do echo "$(date +%s),$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits),$(free -m | awk '/Mem:/{print $3}')" >> $B/recursos.csv; sleep 2; done ) &

# downloads em paralelo (todos de uma vez; o A e o menor e chega primeiro)
baixa() {  # variante destino arquivo url
  local d=$B/models/$2/$3
  if curl -sL --fail --retry 3 -o "$d.part" "$4"; then mv "$d.part" "$d"; log "baixou $3 ($(stat -c %s $d) B)"; return 0; fi
  log "FALHOU $3"; echo "download $3" > $B/falhou_$1; return 1
}
python3 - "$V/downloads.json" > $B/downloads.sh <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
for v, arqs in d.items():
    if not arqs:
        continue
    cmds = " && ".join(f"baixa {v} {dest} {nome} '{url}'" for dest, nome, url in arqs)
    print(f"( {cmds} && touch /root/bench/baixado_{v} ) &")
EOF
source $B/downloads.sh

# ComfyUI v0.37 separado (Qwen-Image-2.1 precisa dele) + no GGUF
PY=""
for p in /workspace/runpod-slim/ComfyUI/.venv/bin/python /opt/venv/bin/python /usr/bin/python3 $(ls -d /*/venv*/bin/python 2>/dev/null); do
  [ -x "$p" ] && "$p" -c "import torch" 2>/dev/null && PY=$p && break
done
log "python com torch: $PY ($($PY -c 'import torch; print(torch.__version__, torch.version.cuda)'))"
git clone -q --depth 1 --branch v0.37.0 https://github.com/comfyanonymous/ComfyUI.git /root/c37
git clone -q --depth 1 https://github.com/city96/ComfyUI-GGUF.git /root/c37/custom_nodes/ComfyUI-GGUF
$PY -m venv /root/c37venv
BASE_SITE=$($PY -c "import site; print(site.getsitepackages()[0])")
echo "$BASE_SITE" > $(/root/c37venv/bin/python -c "import site; print(site.getsitepackages()[0])")/base_torch.pth
/root/c37venv/bin/pip install -q -r /root/c37/requirements.txt gguf >> $B/pip.log 2>&1 || log "pip com avisos (ver pip.log)"
cat > /root/c37/extra_model_paths.yaml <<EOF
volume:
  base_path: /workspace/runpod-slim/ComfyUI/models/
  diffusion_models: |
    diffusion_models
    unet
  text_encoders: |
    text_encoders
    clip
  vae: vae
  loras: loras
bench:
  base_path: $B/models/
  diffusion_models: diffusion_models
  text_encoders: text_encoders
  vae: vae
  loras: loras
EOF
( while [ ! -f $B/fim ] && [ ! -f $B/fim_teto ]; do
    cd /root/c37 && /root/c37venv/bin/python main.py --listen 0.0.0.0 --port 8189 --disable-auto-launch >> $B/comfy.log 2>&1
    echo "$(date +%s) ComfyUI saiu (codigo $?)" >> $B/quedas.log; sleep 5
  done ) &
until curl -s -o /dev/null 127.0.0.1:8189/system_stats; do sleep 3; [ -f $B/fim_teto ] && desligar "teto antes do ComfyUI subir"; done
log "ComfyUI v0.37 no ar: $(curl -s 127.0.0.1:8189/system_stats | head -c 200)"
touch $B/pronto_B $B/pronto_G
# NVFP4: so acelera com torch cu130 - registra o que tem (a variante roda mesmo assim, se carregar)
$PY -c "import torch; print('NVFP4: torch', torch.__version__, 'cuda', torch.version.cuda)"

# marca cada variante quando os arquivos DELA chegaram (E usa os do D)
while [ ! -f $B/fim ] && [ ! -f $B/fim_teto ]; do
  for v in A C D F; do [ -f $B/baixado_$v ] && [ ! -f $B/pronto_$v ] && touch $B/pronto_$v && log "pronto $v"; done
  [ -f $B/pronto_D ] && [ ! -f $B/pronto_E ] && touch $B/pronto_E
  [ -f $B/falhou_D ] && [ ! -f $B/falhou_E ] && cp $B/falhou_D $B/falhou_E
  sleep 5
done
[ -f $B/fim ] && touch $V/CONCLUIDA
desligar "$( [ -f $B/fim ] && echo 'fim pedido pela sessao' || echo 'teto' )"
