# RunPod Serverless ComfyUI worker for Wan 2.2 Animate / character swap.
# Build context: repository root
# Example:
#   docker build --platform linux/amd64 -f runpod/wanimate/Dockerfile -t YOUR_DOCKERHUB/wanimate-comfyui:0.1 .

ARG WORKER_COMFYUI_TAG=5.8.6-base
FROM runpod/worker-comfyui:${WORKER_COMFYUI_TAG}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG INSTALL_SAGEATTENTION=false
ARG BAKE_MODELS=false

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_INPUT=1 \
    COMFY_LOG_LEVEL=INFO

# Build/runtime helpers for onnxruntime, opencv, sageattention builds, and ffmpeg video output.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      python3-dev \
      ninja-build \
      git \
      wget \
      curl \
      ca-certificates \
      ffmpeg \
      libgl1 \
      libglib2.0-0 \
      libsm6 \
      libxext6 \
      libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Install the custom nodes at the local commits known to register this workflow.
RUN set -eux; \
    cd /comfyui/custom_nodes; \
    git clone https://github.com/kijai/ComfyUI-WanVideoWrapper.git; \
    git -C ComfyUI-WanVideoWrapper checkout 088128b224242e110d3906c6750e9a3a348a659b; \
    git clone https://github.com/kijai/ComfyUI-KJNodes.git; \
    git -C ComfyUI-KJNodes checkout 00da1910634fbf314d407608efb281ae6f7f1ba2; \
    git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git; \
    git -C ComfyUI-VideoHelperSuite checkout 0a75c7958fe320efcb052f1d9f8451fd20c730a8; \
    git clone https://github.com/kijai/ComfyUI-WanAnimatePreprocess.git; \
    git -C ComfyUI-WanAnimatePreprocess checkout 0e0b6a2a555625acf4d4aefb780e27d06937132f; \
    git clone https://github.com/kijai/ComfyUI-segment-anything-2.git; \
    git -C ComfyUI-segment-anything-2 checkout 0c35fff5f382803e2310103357b5e985f5437f32

# Python deps for the custom nodes. Keep this explicit so build failures are visible.
RUN set -eux; \
    uv pip install -r /comfyui/custom_nodes/ComfyUI-WanVideoWrapper/requirements.txt; \
    uv pip install -r /comfyui/custom_nodes/ComfyUI-KJNodes/requirements.txt; \
    uv pip install -r /comfyui/custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt; \
    uv pip install -r /comfyui/custom_nodes/ComfyUI-WanAnimatePreprocess/requirements.txt; \
    uv pip install huggingface_hub matplotlib color-matcher imageio-ffmpeg opencv-python-headless onnx onnxruntime-gpu

RUN if [[ "${INSTALL_SAGEATTENTION}" == "true" ]]; then \
      uv pip install ninja packaging sageattention; \
    else \
      echo "Skipping sageattention install. Use client --attention sdpa if you build this way."; \
    fi

# Model downloader can be run either at image build time (BAKE_MODELS=true) or in
# a temporary RunPod pod pointed at a network volume (/runpod-volume).
COPY runpod/wanimate/download_models.sh /opt/wanimate/download_models.sh
RUN chmod +x /opt/wanimate/download_models.sh
RUN if [[ "${BAKE_MODELS}" == "true" ]]; then /opt/wanimate/download_models.sh /comfyui; fi

# Copy custom handler that returns VideoHelperSuite MP4 outputs (`gifs`) as videos.
COPY runpod/wanimate/handler.py /handler.py

# Let endpoint env add flags such as COMFYUI_EXTRA_ARGS=--lowvram for 24GB GPUs.
RUN sed -i 's#python -u /comfyui/main.py --disable-auto-launch --disable-metadata#python -u /comfyui/main.py ${COMFYUI_EXTRA_ARGS:-} --disable-auto-launch --disable-metadata#g' /start.sh

# Build-time import smoke test. CPU only; catches missing Python deps/custom-node syntax errors.
RUN cd /comfyui && timeout 300 python main.py --quick-test-for-ci --cpu

CMD ["/start.sh"]
