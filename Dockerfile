# One image for the orchestrator and the four gRPC services (docker-compose.yml picks the command).
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# tesseract: OCR of scanned PDF pages for the index; DejaVu: the font for PDF exports and edits
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./
COPY backend/ backend/
COPY services/ services/
COPY agentic_rag_proto/ agentic_rag_proto/
COPY protos/ protos/
COPY scripts/ scripts/
COPY --from=web /web/dist frontend/dist
# writable folders for the non-root user: logs, each service's data, the orchestrator's document files
RUN useradd --system --no-create-home app \
    && mkdir -p /app/logs /data/services /data/library \
    && chown -R app /app/logs /data \
    && sed -i 's/\r$//' scripts/docker-entrypoint.sh && chmod +x scripts/docker-entrypoint.sh
# Starts as root only to give the data folders (a Render disk is mounted root-owned) to `app`, then runs as `app`.
ENTRYPOINT ["scripts/docker-entrypoint.sh"]
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
