# RunPod Serverless Wan 2.2 Animate / RunComfy 1307 setup

This folder packages the locally validated RunComfy `Wan 2.2 Animate | Character Swap & Lip-Sync` workflow as a RunPod Serverless ComfyUI endpoint.

It follows the RunPod tutorial pattern from <https://www.runpod.io/blog/deploy-comfyui-as-a-serverless-api-endpoint>, but uses a custom worker because:

- the stock RunPod Hub ComfyUI image is Flux-oriented and does not include WanAnimate models/nodes;
- the stock `worker-comfyui` handler returns `images` only, while VideoHelperSuite MP4 outputs appear in ComfyUI history under `gifs`;
- WanAnimate needs custom nodes and ~33GB of model files.

## Files

- `Dockerfile` — custom image from `runpod/worker-comfyui:5.8.6-base`
- `handler.py` — RunPod handler that stages input videos/images and returns MP4 outputs
- `download_models.sh` — downloads all WanAnimate model files to `/comfyui/models` or `/runpod-volume/models`
- `build_and_push.sh` — optional local Docker build/push helper
- `.env.example` — env template; never commit real keys
- `../../assets/workflows/runpod_wanimate_5f_final_and_diag_api.json` — canonical API workflow
- `../../scripts/runpod_wanimate_client.py` — local client for submitting jobs

## Local PC status

ComfyUI was stopped locally. Port `8188` is closed. Remaining VRAM shown by `nvidia-smi` is WDDM/graphics applications, not ComfyUI.

## Recommended deployment shape

Use a network volume for models. Baking models into Docker also works but creates a huge image.

Recommended GPU: **L40S / A100 40GB / RTX 6000 Ada / A40**. A 24GB GPU may work with offload, but Wan 14B fp8 + preprocess is tight.

Recommended endpoint settings:

- Active Workers: `0`
- Max Workers: start with `1`
- GPUs/Worker: `1`
- Idle Timeout: default/short
- Flash Boot: enabled
- Container Disk:
  - `20 GB` if using network volume models
  - `70+ GB` if baking models into the image
- Env vars:
  - `REFRESH_WORKER=true` for clean state after each job
  - `COMFY_LOG_LEVEL=INFO`
  - optional for 24GB GPUs: `COMFYUI_EXTRA_ARGS=--lowvram`
  - optional debug: `NETWORK_VOLUME_DEBUG=true`
  - optional output-to-bucket: RunPod worker S3 env vars (`BUCKET_ENDPOINT_URL`, etc.)

## Option A — Docker image + network volume models (recommended)

If using RunPod GitHub Integration instead of local Docker, this repo must be pushed to a GitHub repo RunPod can access. Current local remote may not be GitHub. Use:

- Context Path: repository root (`/`)
- Dockerfile Path: `runpod/wanimate/Dockerfile`
- Build arg defaults are fine for network-volume models (`BAKE_MODELS=false`)

### 1. Build/push the custom image

From repo root:

```bash
cp runpod/wanimate/.env.example runpod/wanimate/.env
# edit IMAGE_NAME in runpod/wanimate/.env or export it in shell
export IMAGE_NAME=yourdockerhubuser/wanimate-comfyui:0.1
./runpod/wanimate/build_and_push.sh
```

By default the Dockerfile uses `INSTALL_SAGEATTENTION=false` because the official worker base is a CUDA runtime image and SageAttention may require `nvcc` during build. The client defaults to `sdpa` + no compile for a reliable first serverless deployment.

Optional optimized build attempt:

```bash
export INSTALL_SAGEATTENTION=true
./runpod/wanimate/build_and_push.sh
```

If that succeeds, set client env `WANIMATE_ATTENTION=sageattn` and `WANIMATE_DISABLE_COMPILE=false` or pass `--attention sageattn --no-disable-compile`.

### 2. Create/populate a RunPod network volume

Create a RunPod network volume in the same region as the endpoint, size at least `80 GB`.

Populate it using a temporary pod with the volume mounted and this repo/image available. Inside that pod/container:

```bash
/opt/wanimate/download_models.sh /runpod-volume
```

Expected model layout:

```text
/runpod-volume/models/diffusion_models/Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors
/runpod-volume/models/vae/wan_2.1_vae.safetensors
/runpod-volume/models/text_encoders/umt5-xxl-enc-bf16.safetensors
/runpod-volume/models/clip_vision/clip_vision_h.safetensors
/runpod-volume/models/loras/WanAnimate_relight_lora_fp16.safetensors
/runpod-volume/models/loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors
/runpod-volume/models/sam2/sam2.1_hiera_base_plus.safetensors
/runpod-volume/models/sam2/sam2.1_hiera_base_plus-fp16.safetensors
/runpod-volume/models/detection/vitpose-l-wholebody.onnx
/runpod-volume/models/detection/yolov10m.onnx
```

### 3. Create the Serverless template + endpoint

Either use the RunPod Console:

1. Serverless → New Endpoint
2. Use your custom image, e.g. `yourdockerhubuser/wanimate-comfyui:0.1`
3. Attach the network volume
4. Set env vars from above
5. Deploy and copy the endpoint ID

Or use the GraphQL helper after `RUNPOD_API_KEY`, `IMAGE_NAME`, and optional `NETWORK_VOLUME_ID` are set in `runpod/wanimate/.env`:

```bash
python scripts/runpod_wanimate_deploy.py create-both \
  --gpu-ids AMPERE_48 \
  --workers-max 1
```

Useful GPU IDs from RunPod docs: `AMPERE_24`, `ADA_24`, `AMPERE_48`, `ADA_48_PRO`, `AMPERE_80`, `ADA_80_PRO`. For this workflow prefer 48GB+.

The helper prints `TEMPLATE_ID=...` and `RUNPOD_ENDPOINT_ID=...`; paste those into `runpod/wanimate/.env`.

## Option B — baked model image

This is simpler operationally but produces a very large Docker image:

```bash
export IMAGE_NAME=yourdockerhubuser/wanimate-comfyui:0.1-baked
export BAKE_MODELS=true
./runpod/wanimate/build_and_push.sh
```

Set endpoint container disk to `70+ GB`.

## Client setup

Install local client dependency if needed:

```bash
python -m pip install requests
```

Set secrets locally; do not commit them. Easiest: copy and edit the ignored env file:

```bash
cp runpod/wanimate/.env.example runpod/wanimate/.env
# edit runpod/wanimate/.env:
# RUNPOD_API_KEY=rp_...
# RUNPOD_ENDPOINT_ID=...
```

The client automatically reads `runpod/wanimate/.env`. Shell env vars also work:

```bash
export RUNPOD_API_KEY=rp_...
export RUNPOD_ENDPOINT_ID=...
```

PowerShell equivalent:

```powershell
$env:RUNPOD_API_KEY = "rp_..."
$env:RUNPOD_ENDPOINT_ID = "..."
```

## Validate local setup

This does not use your GPU or start ComfyUI:

```bash
python scripts/check_runpod_wanimate_setup.py
```

Expected before deployment: workflow/model URL checks pass; key/endpoint/image may warn until filled.

## Smoke tests

### Official WanAnimate live-action demo, 5 frames

This is the known-good baseline we validated locally.

```bash
python scripts/runpod_wanimate_client.py \
  --reference-file assets/wan_official_examples/animate/image.jpeg \
  --video-file assets/wan_official_examples/animate/video.mp4 \
  --frames 5 --width 832 --height 480 \
  --prefix official_animate_5f \
  --out-dir outputs/runpod_wanimate/official_animate_5f
```

Expected returned videos:

- `official_animate_5f_final_...mp4` — Wan output
- `official_animate_5f_diag_...mp4` — diagnostic side-by-side/collage

### Official replacement demo, 5 frames

```bash
python scripts/runpod_wanimate_client.py \
  --reference-file assets/wan_official_examples/replace/image.jpeg \
  --video-file assets/wan_official_examples/replace/video.mp4 \
  --frames 5 --width 832 --height 480 \
  --prefix official_replace_5f \
  --out-dir outputs/runpod_wanimate/official_replace_5f
```

### User avatar/source clip, 5 frames

Use the short clip, not the full 15-minute MKV:

```bash
python scripts/runpod_wanimate_client.py \
  --reference-file reference-art/vrc_avatar_t-pose.png \
  --video-file "E:/ComfyUI/ComfyUI_windows_portable_nvidia/ComfyUI_windows_portable/ComfyUI/input/wan_1307_bottom-right_10s_16fps_161f.mp4" \
  --frames 5 --width 832 --height 480 \
  --prefix user_avatar_5f \
  --out-dir outputs/runpod_wanimate/user_avatar_5f
```

The client refuses `--frames < 5` because 1-frame WanAnimate tests were misleading for this graph.

## Larger videos

Do **not** send large/full videos inline as base64. Host them somewhere accessible and pass URLs:

```bash
python scripts/runpod_wanimate_client.py \
  --reference-url https://example.com/reference.png \
  --video-url https://example.com/source_clip.mp4 \
  --frames 17 --width 832 --height 480
```

Full 15-minute MKV processing remains off-limits unless explicitly confirmed.

## Troubleshooting

- If endpoint completes but no videos return: check the endpoint is using `runpod/wanimate/handler.py`, not stock `worker-comfyui` handler.
- If model validation fails: enable `NETWORK_VOLUME_DEBUG=true` and confirm `/runpod-volume/models/...` filenames exactly match the list above.
- The default build/client path is already `INSTALL_SAGEATTENTION=false`, `sdpa`, and compile disabled for reliability. If you later build with SageAttention, submit with `--attention sageattn --no-disable-compile`.
- If first job is very slow: cold start + model load are expected; later jobs on warm workers should be faster.
