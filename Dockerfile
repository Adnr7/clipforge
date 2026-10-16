FROM node:22.12.0-bookworm-slim AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CLIPFORGE_PORT=5174 \
    DATA_DIR=/data

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock

COPY backend/ ./backend/
COPY main.py .env.example LICENSE README.md ./
COPY docs/ ./docs/
COPY scripts/ ./scripts/
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

RUN mkdir -p /data
EXPOSE 5174

CMD ["python", "main.py", "--web"]
