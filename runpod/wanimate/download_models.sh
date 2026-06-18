#!/usr/bin/env bash
# Download the model files required by the RunComfy 1307 Wan 2.2 Animate workflow.
# Usage:
#   ./download_models.sh /comfyui          # bake into image
#   ./download_models.sh /runpod-volume   # populate RunPod network volume
set -euo pipefail

ROOT="${1:-/comfyui}"
MODELS="$ROOT/models"

mkdir -p \
  "$MODELS/diffusion_models" \
  "$MODELS/vae" \
  "$MODELS/text_encoders" \
  "$MODELS/clip_vision" \
  "$MODELS/loras" \
  "$MODELS/sam2" \
  "$MODELS/detection"

hf_header=()
if [[ -n "${HF_TOKEN:-${HUGGINGFACE_ACCESS_TOKEN:-}}" ]]; then
  token="${HF_TOKEN:-${HUGGINGFACE_ACCESS_TOKEN:-}}"
  hf_header=(-H "Authorization: Bearer ${token}")
fi

download() {
  local url="$1"
  local out="$2"
  if [[ -s "$out" ]]; then
    echo "exists: $out"
    return 0
  fi
  echo "downloading: $url"
  echo "        -> $out"
  mkdir -p "$(dirname "$out")"
  # -C - resumes partial downloads; --retry-all-errors handles transient HF/S3 failures.
  curl -L --fail --retry 8 --retry-delay 5 --retry-all-errors -C - "${hf_header[@]}" -o "$out" "$url"
}

# WanVideoWrapper model stack used by RunComfy workflow 1307.
download \
  "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/Wan22Animate/Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors" \
  "$MODELS/diffusion_models/Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors"

download \
  "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan2_1_VAE_bf16.safetensors" \
  "$MODELS/vae/wan_2.1_vae.safetensors"

download \
  "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-bf16.safetensors" \
  "$MODELS/text_encoders/umt5-xxl-enc-bf16.safetensors"

download \
  "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors" \
  "$MODELS/clip_vision/clip_vision_h.safetensors"

# Distill/relight LoRAs expected by the 4-step RunComfy graph.
download \
  "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/LoRAs/Wan22_relight/WanAnimate_relight_lora_fp16.safetensors" \
  "$MODELS/loras/WanAnimate_relight_lora_fp16.safetensors"

download \
  "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors" \
  "$MODELS/loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"

# SAM2 node may select fp16 variant when precision=fp16, so include both names.
download \
  "https://huggingface.co/Kijai/sam2-safetensors/resolve/main/sam2.1_hiera_base_plus.safetensors" \
  "$MODELS/sam2/sam2.1_hiera_base_plus.safetensors"

download \
  "https://huggingface.co/Kijai/sam2-safetensors/resolve/main/sam2.1_hiera_base_plus-fp16.safetensors" \
  "$MODELS/sam2/sam2.1_hiera_base_plus-fp16.safetensors"

# WanAnimate preprocess models.
download \
  "https://huggingface.co/JunkyByte/easy_ViTPose/resolve/main/onnx/wholebody/vitpose-l-wholebody.onnx" \
  "$MODELS/detection/vitpose-l-wholebody.onnx"

download \
  "https://huggingface.co/Wan-AI/Wan2.2-Animate-14B/resolve/main/process_checkpoint/det/yolov10m.onnx" \
  "$MODELS/detection/yolov10m.onnx"

find "$MODELS" -maxdepth 2 -type f -printf '%P\t%s bytes\n' | sort
