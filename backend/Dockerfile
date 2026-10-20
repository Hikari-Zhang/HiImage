FROM python:3.11-slim-bookworm

ARG TORCH_VERSION=2.5.1
ARG TORCHVISION_VERSION=0.20.1
ARG FLUX_DIFFUSERS_VERSION=0.32.2
ARG FLUX_TRANSFORMERS_VERSION=4.47.1
ARG FLUX_HUGGINGFACE_HUB_VERSION=0.27.1
ARG FLUX_PEFT_VERSION=0.14.0

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HIIMAGE_PROJECT_ROOT=/app

WORKDIR /app/backend

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Build context is the repository root. Install CPU wheels first so the generic
# torch constraints in requirements.txt cannot select a CUDA wheel.
COPY backend/requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install \
        "torch==${TORCH_VERSION}" "torchvision==${TORCHVISION_VERSION}" \
        --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install -r /tmp/requirements.txt \
    && python -m pip install --no-deps \
        "diffusers==${FLUX_DIFFUSERS_VERSION}" \
        "transformers==${FLUX_TRANSFORMERS_VERSION}" \
        "huggingface-hub==${FLUX_HUGGINGFACE_HUB_VERSION}" \
        "peft==${FLUX_PEFT_VERSION}"

COPY scripts/post_install.py /app/scripts/post_install.py
RUN python /app/scripts/post_install.py

COPY backend/ /app/backend/

RUN groupadd --gid 10001 hiimage \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin hiimage \
    && mkdir -p /app/config /app/cache /app/tmp /app/output /app/models \
    && chown -R hiimage:hiimage /app /home/hiimage

USER hiimage

EXPOSE 8787

HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=60s \
    CMD curl -fsS http://127.0.0.1:8787/api/health || exit 1

CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8787"]
