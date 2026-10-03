#!/usr/bin/env bash
# Treina a LoRA da Luna para o Qwen-Image-Edit 2511 (pack com a persona) com o
# ai-toolkit, num pod de treino separado (32 GB de VRAM, uint3 + low_vram).
#
# Pre-requisitos no volume (/workspace):
#   lora_qwen_luna/{target,control1,control2}  - scripts/prep_qwen_lora_dataset.py
#   lora_qwen_samples/{c1_*,c2_*}.png          - amostras do pack para acompanhar
# O ai-toolkit e o modelo base (~58 GB) ficam no disco do container (/root):
# o volume nao tem espaco. A LoRA sai em /workspace/lora_qwen_out e a final vai
# para ComfyUI/models/loras. No fim o pod se desliga sozinho (RUNPOD_API_KEY).
set -uo pipefail

AITK=/root/ai-toolkit
OUT=/workspace/lora_qwen_out
NAME=luna_qwen_2511_v1
# 20 s/passo com 512-1024 e controles de 1 MP; 512-768 com controles no
# tamanho do alvo (match_target_res) fica bem mais rapido.
STEPS=${STEPS:-1500}
LOG=/workspace/lora_qwen_train.log
export HF_HOME=/root/hf
export HF_HUB_ENABLE_HF_TRANSFER=1

log() { echo "[train_qwen_lora] $(date +%H:%M:%S) $*" | tee -a "$LOG"; }

stop_pod() {
  log "desligando o pod $RUNPOD_POD_ID"
  sync
  curl -s -X POST -H "Authorization: Bearer $RUNPOD_API_KEY" "https://rest.runpod.io/v1/pods/$RUNPOD_POD_ID/stop" >> "$LOG" 2>&1
}
trap stop_pod EXIT

if [ ! -x "$AITK/venv/bin/python" ]; then
  log "instalando o ai-toolkit"
  git clone -q https://github.com/ostris/ai-toolkit.git "$AITK"
  (cd "$AITK" && git submodule update --init --recursive -q)
  python3 -m venv "$AITK/venv"
  "$AITK/venv/bin/pip" install -q --upgrade pip
  "$AITK/venv/bin/pip" install -q torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
  "$AITK/venv/bin/pip" install -q -r "$AITK/requirements.txt"
  "$AITK/venv/bin/pip" install -q hf_transfer
fi
log "ai-toolkit: $(cd "$AITK" && git log --oneline -1)"

SAMPLES=""
for c1 in /workspace/lora_qwen_samples/c1_*.png; do
  base=$(basename "$c1" .png); base=${base#c1_}
  prompt=$(cat "/workspace/lora_qwen_samples/p_$base.txt" | sed 's/"/\\"/g')
  SAMPLES="$SAMPLES
          - prompt: \"$prompt\"
            ctrl_img_1: \"$c1\"
            ctrl_img_2: \"/workspace/lora_qwen_samples/c2_$base.png\""
done

mkdir -p "$OUT"
cat > "$OUT/$NAME.yaml" <<EOF
job: extension
config:
  name: "$NAME"
  process:
    - type: 'diffusion_trainer'
      training_folder: "$OUT"
      device: cuda:0
      network:
        type: "lora"
        linear: 16
        linear_alpha: 16
      save:
        dtype: float16
        save_every: 250
        max_step_saves_to_keep: 12
      datasets:
        - folder_path: "/workspace/lora_qwen_luna/target"
          control_path:
            - "/workspace/lora_qwen_luna/control1"
            - "/workspace/lora_qwen_luna/control2"
          caption_ext: "txt"
          caption_dropout_rate: 0.05
          resolution: [ 512, 768 ]
      train:
        batch_size: 1
        cache_text_embeddings: true
        steps: $STEPS
        gradient_accumulation: 1
        timestep_type: "weighted"
        train_unet: true
        train_text_encoder: false
        gradient_checkpointing: true
        noise_scheduler: "flowmatch"
        optimizer: "adamw8bit"
        lr: 1e-4
        dtype: bf16
      model:
        name_or_path: "Qwen/Qwen-Image-Edit-2511"
        arch: "qwen_image_edit_plus"
        quantize: true
        qtype: "uint3|ostris/accuracy_recovery_adapters/qwen_image_edit_2511_torchao_uint3.safetensors"
        quantize_te: true
        qtype_te: "qfloat8"
        low_vram: true
        model_kwargs:
          match_target_res: true
      sample:
        sampler: "flowmatch"
        sample_every: 500
        sample_start_step: 0
        skip_first_sample: true
        width: 576
        height: 768
        samples:$SAMPLES
        neg: ""
        seed: 42
        walk_seed: false
        guidance_scale: 3
        sample_steps: 20
meta:
  name: "$NAME"
  version: '1.0'
EOF

log "treinando $STEPS passos"
cd "$AITK" && "$AITK/venv/bin/python" run.py "$OUT/$NAME.yaml" >> "$LOG" 2>&1
status=$?
log "treino terminou (codigo $status)"

final="$OUT/$NAME/$NAME.safetensors"
if [ -f "$final" ]; then
  cp "$final" "/workspace/runpod-slim/ComfyUI/models/loras/$NAME.safetensors"
  log "LoRA final copiada para ComfyUI/models/loras/$NAME.safetensors"
fi
