#!/usr/bin/env bash
# Treina a LoRA da Luna (gatilho "lunavox") no Chroma1-HD com o ai-toolkit.
#
# Pre-requisito: as imagens de treino enviadas para ComfyUI/input/lora_luna/
# (luna_01.jpg ... luna_14.jpg) pela API de upload do ComfyUI.
#
# Tudo pesado (ai-toolkit, modelo base ~18 GB, cache) fica no disco do container
# em /root, porque o volume de rede esta no limite de 40 GB. O resultado final
# e ligado em ComfyUI/models/loras e ComfyUI/output (para download pelo /view).
#
# O auto-desligamento do backend so conta /api/chat e /api/generate como uso,
# entao um laco em segundo plano "cutuca" o backend enquanto o treino roda.
# Quando o treino termina, o laco para e o pod se desliga sozinho ~8 min depois.
set -euo pipefail

COMFY=/workspace/runpod-slim/ComfyUI
WORK=/root/lora
AITK=/root/ai-toolkit
DATA="$WORK/luna"
NAME=luna_chroma_v1
LOG=/tmp/luna-logs/lora_train.log
mkdir -p "$WORK" "$DATA" /tmp/luna-logs

log() { echo "[train_luna_lora] $(date +%H:%M:%S) $*"; }

if [ ! -d "$AITK" ]; then
  log "Instalando ai-toolkit"
  git clone -q https://github.com/ostris/ai-toolkit.git "$AITK"
  (cd "$AITK" && git submodule update --init --recursive -q)
fi
if [ ! -x "$AITK/venv/bin/python" ]; then
  python3 -m venv "$AITK/venv"
  "$AITK/venv/bin/pip" install -q --upgrade pip
  "$AITK/venv/bin/pip" install -q torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
  "$AITK/venv/bin/pip" install -q -r "$AITK/requirements.txt"
fi

log "Montando o dataset em $DATA"
cp "$COMFY"/input/lora_luna/*.jpg "$DATA"/
"$AITK/venv/bin/python" - "$DATA" <<'EOF'
import os, sys
# Legendas descrevem so o que VARIA (roupa, cena, textos de fundo). Rosto,
# cabelo, colares, pingente e tatuagem ficam de fora de proposito: assim a
# LoRA aprende esses tracos como parte de "lunavox".
captions = {
    "luna_01": "photo of lunavox at a crowded baile funk party at night, wearing a black crop top and denim shorts, holding a plastic cup, other people dancing around her, graffiti wall with the text 'FUNK E CULTURA' in the background",
    "luna_02": "photo of lunavox from behind looking over her shoulder, wearing a black string bikini, standing by a rooftop infinity pool in Rio de Janeiro, sugarloaf mountain in the background, wall sign with text, black towel with a 'LUNA VOX' logo on a lounge chair",
    "luna_03": "photo of lunavox lying on a sofa leaning toward the camera, wearing an open cream knit cardigan over black lace lingerie, cozy apartment at golden hour, framed poster with the text 'LUNA VOX' on the wall, black mug with text, laptop and a notebook with a checklist in the foreground",
    "luna_04": "full body photo of lunavox standing, wearing a white tank top, light blue wide leg jeans and white sneakers, plain grey studio background",
    "luna_05": "photo of lunavox sitting on a lounge chair by a rooftop pool, wearing a black bikini, legs crossed, glass of aperol spritz, black towel with a 'LUNA VOX' logo, chalkboard sign with text, sunny day",
    "luna_06": "full body photo of lunavox standing facing the camera, wearing a white cropped t-shirt, light blue straight jeans and white sneakers, plain grey studio background",
    "luna_07": "full body photo of lunavox seen from behind, wearing a white cropped t-shirt, light blue straight jeans and white sneakers, plain grey studio background",
    "luna_08": "close-up portrait photo of lunavox looking at the camera, neutral expression, wearing a black tank top, plain grey background",
    "luna_09": "mirror selfie of lunavox in a stainless steel elevator, holding a black phone, wearing a white tank top and black shorts, black backpack, elevator sign with text",
    "luna_10": "photo of lunavox walking on Avenida Paulista in Sao Paulo, wearing a white tank top and denim shorts, holding an iced coffee cup, black shoulder bag, crowd and a metro station sign in the background, sunny day",
    "luna_11": "photo of lunavox laughing at a bar at night, holding a glass of beer, wearing a sheer black top over a black bra, people in the background, warm bokeh lights",
    "luna_12": "photo of lunavox in a bookstore reading a book with a black cover, wearing a beige button cardigan and jeans, black shoulder bag, bookshelves and a sign with text in the background",
    "luna_13": "photo of lunavox standing in a home office next to an indoor pool, wearing a colorful floral bikini with black lace trim, holding a microphone, computer monitor with video editing software, framed sign with a 'LUNA VOX' logo, whiteboard with a content plan, city skyline through the windows",
    "luna_14": "full body photo of lunavox in a walk-in closet choosing an outfit, wearing a black tank top and grey shorts, barefoot, holding a white dress on a hanger, clothes on racks",
}
d = sys.argv[1]
for name, text in captions.items():
    if not os.path.exists(os.path.join(d, name + ".jpg")):
        raise SystemExit(f"imagem faltando: {name}.jpg")
    with open(os.path.join(d, name + ".txt"), "w") as f:
        f.write(text)
print(f"{len(captions)} legendas escritas")
EOF

cat > "$WORK/$NAME.yaml" <<EOF
job: extension
config:
  name: "$NAME"
  process:
    - type: 'sd_trainer'
      training_folder: "$WORK/output"
      device: cuda:0
      trigger_word: "lunavox"
      network:
        type: "lora"
        linear: 32
        linear_alpha: 32
      save:
        dtype: float16
        save_every: 250
        max_step_saves_to_keep: 8
      datasets:
        - folder_path: "$DATA"
          caption_ext: "txt"
          caption_dropout_rate: 0.05
          shuffle_tokens: false
          cache_latents_to_disk: true
          resolution: [ 512, 768, 1024 ]
      train:
        batch_size: 1
        steps: 2000
        gradient_accumulation: 1
        train_unet: true
        train_text_encoder: false
        gradient_checkpointing: true
        noise_scheduler: "flowmatch"
        optimizer: "adamw8bit"
        lr: 1e-4
        ema_config:
          use_ema: true
          ema_decay: 0.99
        dtype: bf16
        skip_first_sample: true
      model:
        name_or_path: "lodestones/Chroma1-HD"
        arch: "chroma"
        quantize: true
      sample:
        sampler: "flowmatch"
        # Cada imagem de teste leva ~3 min numa L4: poucas e espacadas.
        sample_every: 500
        width: 832
        height: 1216
        prompts:
          - "close-up portrait photo of lunavox smiling at the camera, soft window light, cafe background"
          - "full body photo of lunavox walking on a beach at sunset, wearing a white summer dress"
        neg: ""
        seed: 42
        walk_seed: false
        guidance_scale: 4
        sample_steps: 20
meta:
  name: "[name]"
  version: '1.0'
EOF

log "Liberando a VRAM do ComfyUI"
curl -s -X POST localhost:8188/free -H "Content-Type: application/json" -d '{"unload_models":true,"free_memory":true}' || true

log "Treinando (log em $LOG)"
cd "$AITK"
HF_HUB_ENABLE_HF_TRANSFER=0 nohup "$AITK/venv/bin/python" run.py "$WORK/$NAME.yaml" > "$LOG" 2>&1 &
TRAIN_PID=$!
echo "$TRAIN_PID" > "$WORK/train.pid"

(
  while kill -0 "$TRAIN_PID" 2>/dev/null; do
    curl -s -o /dev/null -X POST localhost:8000/api/generate -H "Content-Type: application/json" \
      -d '{"prompt":"keepalive","model_id":"__keepalive__"}' || true
    sleep 120
  done
  OUT="$WORK/output/$NAME"
  mkdir -p "$COMFY/models/loras" "$COMFY/output/lora"
  for f in "$OUT"/*.safetensors; do
    [ -e "$f" ] || continue
    ln -sfn "$f" "$COMFY/models/loras/$(basename "$f")"
    ln -sfn "$f" "$COMFY/output/lora/$(basename "$f")"
  done
  if [ -d "$OUT/samples" ]; then ln -sfn "$OUT/samples" "$COMFY/output/lora/samples"; fi
  echo "[train_luna_lora] $(date +%H:%M:%S) treino terminou, LoRAs ligadas em models/loras" >> "$LOG"
) > /dev/null 2>&1 &
disown

log "Treino iniciado (pid $TRAIN_PID). Acompanhe com: tail -f $LOG"
