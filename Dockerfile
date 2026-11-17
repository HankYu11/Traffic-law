# ---- 前端 ----
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 後端（含向量模型，內網可離線執行）----
FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 MODEL_CACHE=/app/models/fastembed
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# 建置時預先下載 EmbeddingGemma（ONNX），映像檔帶進內網後不需再連外
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('google/embeddinggemma-300m', cache_dir='/app/models/fastembed')"
COPY backend/ backend/
COPY ingest/ ingest/
COPY eval/ eval/
COPY data/ data/
COPY --from=web /web/dist frontend/dist
RUN mkdir -p /app/state
WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
