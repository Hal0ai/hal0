# hal0-toolbox-qwen3tts — Qwen3-TTS (CustomVoice) GPU TTS backend, ROCm.
#
# A multilingual (10-language) voice engine that runs alongside the Kokoro
# TTS slot. Implements the same OpenAI /v1/audio/speech contract
# (packaging/toolbox/qwen3tts/qwen3tts_server.py) so it is a drop-in
# alternative voice for Hermes / Open WebUI.
#
# Base: rocm/pytorch matching the host ROCm the agent slot already uses
# (ROCm 7.2.4 on the Strix Halo gfx1151 iGPU) with PyTorch 2.10 prebuilt.
# We deliberately DO NOT install flash-attn (no clean ROCm build); the
# server omits attn_implementation so transformers falls back to sdpa/eager.
#
# Build (podman, so the image lands in the store the slot units use):
#   podman build -t ghcr.io/hal0ai/hal0-toolbox-qwen3tts:v1 \
#       -f packaging/toolbox/qwen3tts.Dockerfile packaging/toolbox/qwen3tts
#
# Run (GPU passthrough mirrors the agent slot):
#   podman run --rm --device=/dev/kfd --device=/dev/dri/renderD128 \
#       --device=/dev/dri/amdgpu --group-add=993 --group-add=44 \
#       --security-opt=apparmor=unconfined --security-opt=seccomp=unconfined \
#       -v /mnt/ai-models:/mnt/ai-models:ro,z -v /var/lib/hal0/qwen3tts-cache:/cache:z \
#       --publish=127.0.0.1:8087:8087 ghcr.io/hal0ai/hal0-toolbox-qwen3tts:v1 \
#       --model_path /mnt/ai-models/local/qwen3-tts/Qwen3-TTS-12Hz-1.7B-CustomVoice \
#       --host 0.0.0.0 --port 8087

FROM docker.io/rocm/pytorch:rocm7.2.4_ubuntu24.04_py3.12_pytorch_release_2.10.0

ARG DEBIAN_FRONTEND=noninteractive

# ffmpeg for mp3/opus encode + atempo (speed); libsndfile for soundfile.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install the TTS stack. torch (ROCm 2.10) is already in the base image and
# satisfies qwen-tts's `torch>=` requirement, so pip leaves it untouched and
# does NOT pull a CPU/CUDA wheel over it. Pin nothing else to a CUDA build.
RUN python3 -m pip install --no-cache-dir \
        qwen-tts \
        "fastapi>=0.110" \
        "uvicorn[standard]>=0.29" \
        soundfile \
    && python3 -c "import torch; print('torch', torch.__version__, 'hip', getattr(torch.version,'hip',None))"

# HF cache on a mountable, writable path so the codec/tokenizer (resolved by
# id at load time) is fetched once and reused across restarts.
ENV HF_HOME=/cache \
    HF_HUB_ENABLE_HF_TRANSFER=0 \
    PYTHONUNBUFFERED=1

COPY qwen3tts_server.py /opt/qwen3tts/qwen3tts_server.py

RUN mkdir -p /cache && chmod 0777 /cache

EXPOSE 8087

# ENTRYPOINT runs the server; ContainerSpec/systemd command[] carries flags only.
ENTRYPOINT ["python3", "/opt/qwen3tts/qwen3tts_server.py"]
CMD ["--help"]
