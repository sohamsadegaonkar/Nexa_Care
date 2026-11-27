# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-trixie@sha256:4a68d3169325c47e1bafbb86425220f03fbdff6f079a419e8d39407183288217 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY requirements.txt ./requirements.txt
COPY requirements-azure.txt ./requirements-azure.txt
ARG INSTALL_AZURE_SDK=false

# Install runtime dependencies into an isolated virtual environment so build
# tooling (pip, setuptools, wheel) is not shipped in the production runtime image.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/python -m pip install -r requirements.txt \
    && case "$INSTALL_AZURE_SDK" in \
        true) /opt/venv/bin/python -m pip install -r requirements-azure.txt ;; \
        false) ;; \
        *) exit 1 ;; \
    esac \
    && /opt/venv/bin/python -m pip check \
    && /opt/venv/bin/python -m pip uninstall -y pip setuptools wheel

FROM python:3.12-slim-trixie@sha256:4a68d3169325c47e1bafbb86425220f03fbdff6f079a419e8d39407183288217 AS runtime

# Runtime hygiene: no bytecode writes, immediate logs, and no pip cache bloat.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app

WORKDIR /app

# No local OCR/ML system packages are installed. Document AI calls are remote.
# Remove base-image pip/ensurepip (including vendored SBOM metadata) not needed at runtime.
RUN rm -rf /usr/local/lib/python3.12/site-packages/pip* /usr/local/lib/python3.12/ensurepip

COPY --from=builder /opt/venv /opt/venv

# Run as a non-root user in production.
RUN groupadd --system nexa_user \
    && useradd --system --gid nexa_user --create-home --home-dir /home/nexa_user nexa_user

COPY --chown=nexa_user:nexa_user . /app

USER nexa_user

EXPOSE 8000

CMD ["sh", "/app/scripts/container_start.sh"]
