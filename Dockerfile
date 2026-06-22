# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS runtime

# Runtime hygiene: no bytecode writes, immediate logs, and no pip cache bloat.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

# System packages required by pdf2image/PIL/image processing.
# - poppler-utils: required by pdf2image for PDF page conversion.
# - libgl1: common image-processing runtime libs (replaces old mesa-glx).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for Docker layer caching.
COPY requirements.txt ./requirements.txt

# Install CPU-only PyTorch wheels before the rest of the Python stack.
# This prevents cloud builds from accidentally pulling GPU/CUDA artifacts.
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        torch==2.11.0+cpu \
        torchvision==0.26.0+cpu \
        torchaudio==2.11.0+cpu \
    && python -m pip install -r requirements.txt

# Run as a non-root user in production.
RUN groupadd --system nexa_user \
    && useradd --system --gid nexa_user --create-home --home-dir /home/nexa_user nexa_user

COPY --chown=nexa_user:nexa_user . /app

USER nexa_user

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]