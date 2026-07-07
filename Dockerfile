# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS runtime

# Runtime hygiene: no bytecode writes, immediate logs, and no pip cache bloat.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

# No local OCR/ML system packages are installed. Document AI calls are remote.

# Copy requirements first for Docker layer caching.
COPY requirements.txt ./requirements.txt

# Install the lightweight remote-API dependency set. Local PyTorch and
# Transformers are intentionally excluded for 512MB free-tier deployments.
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -r requirements.txt

# Run as a non-root user in production.
RUN groupadd --system nexa_user \
    && useradd --system --gid nexa_user --create-home --home-dir /home/nexa_user nexa_user

COPY --chown=nexa_user:nexa_user . /app

USER nexa_user

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]