# SoundBubble cluster image: FastAPI API + built React UI + ASR + embeddings + Elasticsearch client.
# CPU-only. Target-speaker extraction is NOT included here (SB_ENABLE_TSE=0): it runs on the
# laptop (see README "Architecture").
FROM node:20-bookworm-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.10-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/opt/hf PORT=8080 \
    SB_ENABLE_TSE=0 SB_DATA_DIR=/data SB_DEPLOYMENT=kubernetes
RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.cluster.txt backend/
RUN pip install -r backend/requirements.cluster.txt
# Bake the embedding + ASR models into the image so pods start without downloading.
RUN python -c "from sentence_transformers import SentenceTransformer as S; S('sentence-transformers/all-MiniLM-L6-v2')" \
 && python -c "from faster_whisper import WhisperModel as W; W('base.en', device='cpu', compute_type='int8')" \
 && chmod -R a+rX /opt/hf
COPY backend backend
COPY --from=ui /ui/dist frontend/dist
RUN mkdir -p /data && chown 1000:1000 /data
USER 1000
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn soundbubble.app:app --app-dir backend --host 0.0.0.0 --port ${PORT}"]
