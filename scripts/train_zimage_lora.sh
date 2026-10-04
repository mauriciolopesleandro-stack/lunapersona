#!/usr/bin/env bash
# Treina a LoRA da Luna no Z-Image Turbo com o ai-toolkit, no proprio pod do
# estudio (RTX PRO 4000, 24 GB: transformer e leitor de texto em qfloat8,
# low_vram). Modo "zimage:turbo" do ai-toolkit: treina com o adaptador de
# treino da ostris por cima do Turbo e a LoRA continua rodando em 8 passos.
#
# Pre-requisitos (no disco do container, /root - o volume esta cheio):
#   /root/ai-toolkit/venv                   - instalado como no train_qwen_lora.sh
#   /root/zimage_dataset/{*.png,*.txt}      - scripts/caption_zimage_dataset.py
# Saida: /root/zimage_lora_out; a final vai para ComfyUI/models/loras/luna_zimage_v1.safetensors
# e as amostras para /workspace/zimage_lora_samples. No fim roda um teste na
# ComfyUI (zimage-txt2img-lora) e libera o auto-desligar (tira o keepalive).
set -uo pipefail

AITK=/root/ai-toolkit
DATA=/root/zimage_dataset
OUT=/root/zimage_lora_out
NAME=luna_zimage_v1
STEPS=${STEPS:-2500}
LOG=/workspace/zimage_lora_train.log
LORAS=/workspace/runpod-slim/ComfyUI/models/loras
export HF_HOME=/root/hf
export HF_HUB_ENABLE_HF_TRANSFER=1

log() { echo "[train_zimage_lora] $(date +%H:%M:%S) $*" | tee -a "$LOG"; }

[ -x "$AITK/venv/bin/python" ] || { log "falta o ai-toolkit em $AITK"; exit 1; }
[ "$(ls "$DATA"/*.txt 2>/dev/null | wc -l)" -gt 10 ] || { log "falta o dataset em $DATA"; exit 1; }

# A ComfyUI segura a VRAM dos modelos carregados: sem liberar, o treino nao cabe.
curl -s -X POST -H "Content-Type: application/json" -d '{"unload_models": true, "free_memory": true}' \
  http://127.0.0.1:8188/free >/dev/null || true

mkdir -p "$OUT"
cat > "$OUT/$NAME.yaml" <<EOF
job: extension
config:
  name: "$NAME"
  process:
    - type: 'diffusion_trainer'
      training_folder: "$OUT"
      device: cuda:0
      trigger_word: "lunavox"
      network:
        type: "lora"
        linear: 32
        linear_alpha: 32
      save:
        dtype: float16
        save_every: 500
        max_step_saves_to_keep: 6
      datasets:
        - folder_path: "$DATA"
          caption_ext: "txt"
          caption_dropout_rate: 0.05
          shuffle_tokens: false
          cache_latents_to_disk: true
          resolution: [ 512, 768, 1024 ]
      train:
        batch_size: 1
        steps: $STEPS
        gradient_accumulation: 1
        train_unet: true
        train_text_encoder: false
        gradient_checkpointing: true
        noise_scheduler: "flowmatch"
        timestep_type: "weighted"
        unload_text_encoder: false
        optimizer: "adamw8bit"
        lr: 1e-4
        dtype: bf16
        ema_config:
          use_ema: false
      model:
        name_or_path: "Tongyi-MAI/Z-Image-Turbo"
        arch: "zimage:turbo"
        assistant_lora_path: "ostris/zimage_turbo_training_adapter/zimage_turbo_training_adapter_v2.safetensors"
        quantize: true
        qtype: "qfloat8"
        quantize_te: true
        qtype_te: "qfloat8"
        low_vram: true
      sample:
        sampler: "flowmatch"
        sample_every: 500
        width: 768
        height: 1152
        prompts:
          - "lunavox, a woman, candid smartphone photo of her laughing on the Copacabana boardwalk at sunset, wearing a white summer dress"
          - "lunavox, a woman, close-up portrait looking at the camera, natural window light, realistic skin texture"
          - "lunavox, a woman, full body photo sitting in a cafe in Sao Paulo holding a cup of coffee, wearing jeans and a black top"
          - "lunavox, a woman, medium shot cooking in a home kitchen, smiling, wearing a red dress"
        neg: ""
        seed: 42
        walk_seed: false
        guidance_scale: 1
        sample_steps: 9
meta:
  name: "$NAME"
  version: "1.0"
EOF

log "treino: $STEPS passos, $(ls "$DATA"/*.txt | wc -l) fotos"
cd "$AITK" && ./venv/bin/python run.py "$OUT/$NAME.yaml" >> "$LOG" 2>&1
status=$?
log "treino terminou (status $status)"

final=$(ls -t "$OUT/$NAME"/*.safetensors 2>/dev/null | head -1)
if [ -n "$final" ]; then
  cp "$final" "$LORAS/$NAME.safetensors" && log "LoRA: $final -> $LORAS/$NAME.safetensors"
fi
mkdir -p /workspace/zimage_lora_samples
cp "$OUT/$NAME"/samples/* /workspace/zimage_lora_samples/ 2>/dev/null
log "amostras em /workspace/zimage_lora_samples ($(ls /workspace/zimage_lora_samples | wc -l))"

# Teste na ComfyUI (o mesmo caminho do site): cenas do calcadao com a LoRA.
if [ -f "$LORAS/$NAME.safetensors" ]; then
  /workspace/runpod-slim/ComfyUI/.venv-cu128/bin/python /workspace/lunapersona/scripts/test_fast_pipeline.py lora >> "$LOG" 2>&1
  log "teste na ComfyUI: $(tail -1 "$LOG")"
fi

# Aviso no celular (Telegram, se conectado em Meu perfil) com o teste.
SHEET=""
if [ -f /workspace/fast_lora.jpg ]; then
  cp /workspace/fast_lora.jpg /workspace/runpod-slim/ComfyUI/output/zimage_lora_teste.jpg
  SHEET="https://${RUNPOD_POD_ID}-8188.proxy.runpod.net/view?filename=zimage_lora_teste.jpg&type=output&preview=jpeg;85"
fi
(cd /workspace/lunapersona/backend && ../.venv-persist/bin/python3 -c "
import asyncio, sys
from app.notify import send
ok = asyncio.run(send(sys.argv[1], photo=sys.argv[2] or None))
print('aviso enviado' if ok else 'aviso nao enviado')
" "🎓 LoRA da Luna no Z-Image pronta (status $status). Em cima: sem LoRA; embaixo: com a LoRA." "$SHEET") >> "$LOG" 2>&1

# Libera o auto-desligar do estudio (8 min parado).
rm -f /workspace/keepalive_claude.on
log "fim"
