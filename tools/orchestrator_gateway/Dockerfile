FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GATEWAY_BIND=0.0.0.0 PORT=8080 GATEWAY_JOURNAL_PATH=/data/journal.db
WORKDIR /srv/gateway
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home gateway && mkdir /data && chown gateway:gateway /data
COPY gateway ./gateway
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "gateway"]
