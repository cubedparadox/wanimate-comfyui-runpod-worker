#!/usr/bin/env python3
"""RunPod serverless handler for ComfyUI WanAnimate workflows.

This is a small extension of the official worker-comfyui pattern that:
- accepts per-request input files (image/video) by URL or base64 and writes them
  into /comfyui/input;
- queues an API-format ComfyUI workflow;
- returns both normal image outputs and VideoHelperSuite MP4 outputs, which appear
  in ComfyUI history under the `gifs` key.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import pathlib
import subprocess
import tempfile
import time
import traceback
import urllib.parse
import uuid
from typing import Any

import requests
import runpod
from runpod.serverless.utils import rp_upload
import websocket

COMFY_HOST = os.environ.get("COMFY_HOST", "127.0.0.1:8188")
COMFY_INPUT_DIR = pathlib.Path(os.environ.get("COMFY_INPUT_DIR", "/comfyui/input"))
POLL_INTERVAL_SECONDS = float(os.environ.get("COMFY_POLL_INTERVAL_SECONDS", "1"))
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "60"))
MODEL_ROOT = pathlib.Path(os.environ.get("MODEL_ROOT", "/runpod-volume" if pathlib.Path("/runpod-volume").exists() else "/comfyui"))
MODEL_DOWNLOADER = pathlib.Path(os.environ.get("MODEL_DOWNLOADER", "/opt/wanimate/download_models.sh"))
AUTO_DOWNLOAD_MODELS = os.environ.get("AUTO_DOWNLOAD_MODELS", "true").lower() == "true"

REQUIRED_MODEL_FILES = [
    "models/diffusion_models/Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors",
    "models/vae/wan_2.1_vae.safetensors",
    "models/text_encoders/umt5-xxl-enc-bf16.safetensors",
    "models/clip_vision/clip_vision_h.safetensors",
    "models/loras/WanAnimate_relight_lora_fp16.safetensors",
    "models/loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors",
    "models/sam2/sam2.1_hiera_base_plus.safetensors",
    "models/sam2/sam2.1_hiera_base_plus-fp16.safetensors",
    "models/detection/vitpose-l-wholebody.onnx",
    "models/detection/yolov10m.onnx",
]


def _strip_data_uri(value: str) -> str:
    return value.split(",", 1)[1] if "," in value and value[:80].lower().startswith("data:") else value


def _safe_input_name(name: str) -> str:
    # Keep requests from writing outside /comfyui/input. Workflows in this repo
    # reference simple filenames, so basename is sufficient and predictable.
    clean = pathlib.PurePosixPath(name.replace("\\", "/")).name
    if not clean or clean in {".", ".."}:
        raise ValueError(f"Invalid input filename: {name!r}")
    return clean


def _write_input_file(spec: dict[str, Any]) -> str:
    name = _safe_input_name(str(spec.get("name") or ""))
    dest = COMFY_INPUT_DIR / name
    dest.parent.mkdir(parents=True, exist_ok=True)

    if spec.get("url"):
        url = str(spec["url"])
        print(f"wanimate-worker - downloading input {name} from {url}")
        with requests.get(url, stream=True, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            response.raise_for_status()
            with dest.open("wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
    else:
        data = spec.get("data") or spec.get("image") or spec.get("file") or spec.get("content")
        if not data:
            raise ValueError(f"Input file {name!r} must include either url or base64 data")
        print(f"wanimate-worker - writing base64 input {name}")
        dest.write_bytes(base64.b64decode(_strip_data_uri(str(data))))

    print(f"wanimate-worker - input ready: {dest} ({dest.stat().st_size} bytes)")
    return name


def stage_input_files(job_input: dict[str, Any]) -> list[str]:
    staged: list[str] = []

    # Official worker-comfyui calls this field `images`; support it for
    # compatibility, but write directly so non-image files work too.
    for image in job_input.get("images") or []:
        staged.append(_write_input_file(image))

    # Extended field for videos and arbitrary files.
    for file_spec in job_input.get("files") or []:
        staged.append(_write_input_file(file_spec))

    return staged


def missing_models() -> list[str]:
    return [rel for rel in REQUIRED_MODEL_FILES if not (MODEL_ROOT / rel).is_file()]


def ensure_models(force: bool = False) -> dict[str, Any]:
    missing = missing_models()
    if not force and not missing:
        print(f"wanimate-worker - all required models already exist under {MODEL_ROOT}")
        return {"status": "ready", "model_root": str(MODEL_ROOT), "missing": []}
    if not MODEL_DOWNLOADER.exists():
        raise RuntimeError(f"Model downloader not found: {MODEL_DOWNLOADER}")
    print(f"wanimate-worker - ensuring models under {MODEL_ROOT}; missing count={len(missing)}")
    if missing:
        print("wanimate-worker - missing models: " + ", ".join(missing))
    started = time.time()
    subprocess.run([str(MODEL_DOWNLOADER), str(MODEL_ROOT)], check=True)
    elapsed = round(time.time() - started, 1)
    missing_after = missing_models()
    status = "ready" if not missing_after else "missing_after_download"
    return {
        "status": status,
        "model_root": str(MODEL_ROOT),
        "elapsed_seconds": elapsed,
        "missing": missing_after,
    }


def check_server() -> None:
    url = f"http://{COMFY_HOST}/"
    print(f"wanimate-worker - waiting for ComfyUI at {url}")
    for attempt in range(900):
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                print("wanimate-worker - ComfyUI is reachable")
                return
        except requests.RequestException:
            pass
        if attempt and attempt % 30 == 0:
            print(f"wanimate-worker - still waiting for ComfyUI ({attempt}s)")
        time.sleep(1)
    raise RuntimeError("ComfyUI server did not become reachable")


def queue_workflow(workflow: dict[str, Any], client_id: str) -> str:
    payload = {"prompt": workflow, "client_id": client_id}
    response = requests.post(
        f"http://{COMFY_HOST}/prompt",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        print("wanimate-worker - ComfyUI /prompt error:", response.text)
    response.raise_for_status()
    result = response.json()
    prompt_id = result.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not return prompt_id: {result}")
    print(f"wanimate-worker - queued prompt {prompt_id}")
    return prompt_id


def wait_for_prompt(prompt_id: str, client_id: str) -> None:
    ws_url = f"ws://{COMFY_HOST}/ws?clientId={client_id}"
    print(f"wanimate-worker - connecting websocket {ws_url}")
    ws = websocket.WebSocket()
    ws.connect(ws_url, timeout=30)
    try:
        while True:
            raw = ws.recv()
            if not isinstance(raw, str):
                continue
            message = json.loads(raw)
            msg_type = message.get("type")
            data = message.get("data") or {}
            if msg_type == "executing" and data.get("prompt_id") == prompt_id and data.get("node") is None:
                print(f"wanimate-worker - prompt finished {prompt_id}")
                return
            if msg_type == "execution_error" and data.get("prompt_id") == prompt_id:
                raise RuntimeError(
                    "ComfyUI execution error: "
                    f"node={data.get('node_id')} type={data.get('node_type')} "
                    f"message={data.get('exception_message')}"
                )
            if msg_type == "status":
                remaining = (data.get("status") or {}).get("exec_info", {}).get("queue_remaining")
                print(f"wanimate-worker - queue remaining: {remaining}")
    finally:
        try:
            ws.close()
        except Exception:
            pass


def get_history(prompt_id: str) -> dict[str, Any]:
    response = requests.get(f"http://{COMFY_HOST}/history/{prompt_id}", timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    history = response.json()
    if prompt_id not in history:
        raise RuntimeError(f"Prompt {prompt_id} missing from history")
    return history[prompt_id]


def fetch_output_bytes(info: dict[str, Any]) -> bytes:
    params = urllib.parse.urlencode(
        {
            "filename": info.get("filename"),
            "subfolder": info.get("subfolder", ""),
            "type": info.get("type", "output"),
        }
    )
    url = f"http://{COMFY_HOST}/view?{params}"
    print(f"wanimate-worker - fetching output {url}")
    response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.content


def encode_or_upload(job_id: str, filename: str, payload: bytes) -> dict[str, Any]:
    suffix = pathlib.Path(filename).suffix or ".bin"
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    if os.environ.get("BUCKET_ENDPOINT_URL"):
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(payload)
            tmp_path = tmp.name
        try:
            # RunPod's helper name says image, but it uploads a file path and is
            # used here for MP4s too. If your bucket rejects this, switch to a
            # boto3 upload in this function.
            url = rp_upload.upload_image(job_id, tmp_path)
            return {"filename": filename, "type": "s3_url", "mime": mime, "data": url}
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    return {
        "filename": filename,
        "type": "base64",
        "mime": mime,
        "data": base64.b64encode(payload).decode("ascii"),
    }


def collect_outputs(job_id: str, history: dict[str, Any]) -> dict[str, Any]:
    outputs = history.get("outputs") or {}
    result: dict[str, Any] = {"images": [], "videos": [], "other": []}

    for node_id, node_output in outputs.items():
        for image_info in node_output.get("images") or []:
            if image_info.get("type") == "temp":
                continue
            filename = image_info.get("filename") or f"node_{node_id}.png"
            result["images"].append(encode_or_upload(job_id, filename, fetch_output_bytes(image_info)))

        # VideoHelperSuite stores mp4/webm/gif output here.
        for video_info in node_output.get("gifs") or []:
            if video_info.get("type") == "temp":
                continue
            filename = video_info.get("filename") or f"node_{node_id}.mp4"
            encoded = encode_or_upload(job_id, filename, fetch_output_bytes(video_info))
            encoded["format"] = video_info.get("format")
            encoded["node_id"] = node_id
            result["videos"].append(encoded)

        for key, value in node_output.items():
            if key not in {"images", "gifs"}:
                result["other"].append({"node_id": node_id, "key": key, "value": value})

    # Keep the response compact.
    return {k: v for k, v in result.items() if v}


def handler(job: dict[str, Any]) -> dict[str, Any]:
    job_id = str(job.get("id") or uuid.uuid4())
    try:
        job_input = job.get("input") or {}
        mode = str(job_input.get("mode") or "run")
        workflow = job_input.get("workflow")

        check_server()

        if mode in {"download_models", "ensure_models"}:
            return {"models": ensure_models(force=mode == "download_models")}

        if not isinstance(workflow, dict):
            return {"error": "Missing input.workflow API-format ComfyUI prompt"}

        if AUTO_DOWNLOAD_MODELS:
            model_status = ensure_models(force=False)
            if model_status.get("missing"):
                return {"error": "Required models are missing after download attempt", "models": model_status}

        staged = stage_input_files(job_input)
        if staged:
            print(f"wanimate-worker - staged inputs: {staged}")

        client_id = str(uuid.uuid4())
        prompt_id = queue_workflow(workflow, client_id)
        wait_for_prompt(prompt_id, client_id)
        history = get_history(prompt_id)
        result = collect_outputs(job_id, history)
        result["prompt_id"] = prompt_id
        if not result.get("images") and not result.get("videos"):
            result["warning"] = "Workflow completed but produced no returned image/video outputs"
        return result
    except Exception as exc:
        print("wanimate-worker - ERROR", exc)
        print(traceback.format_exc())
        return {"error": str(exc), "traceback": traceback.format_exc()}


if __name__ == "__main__":
    print("wanimate-worker - starting RunPod handler")
    runpod.serverless.start({"handler": handler})
