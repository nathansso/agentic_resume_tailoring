# Stage 1: build React frontend
# Node 24 bundles npm 11, matching the dev machine — npm 10 and 11 write
# platform-conditional optional deps into package-lock.json differently, and
# the version skew repeatedly broke `npm ci` here (EUSAGE).
FROM node:24-slim AS frontend
WORKDIR /app/web/frontend
COPY web/frontend/package*.json ./
RUN npm ci
COPY web/frontend/ ./
RUN npm run build

# Stage 2: Python runtime
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 libxslt1.1 libffi-dev \
    gcc python3-dev pkg-config \
    texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-core.txt .
RUN pip install --no-cache-dir -r requirements-core.txt

RUN python -c "import nltk; nltk.download('stopwords', quiet=True); nltk.download('punkt_tab', quiet=True)"

COPY . .

# React build output (vite outDir: "../static") → served by FastAPI StaticFiles
COPY --from=frontend /app/web/static/ ./web/static/

ENV ART_DATA_DIR=/data

# /data is provided by the platform (fly.toml [[mounts]] / Railway volume).
# Do not add a Docker VOLUME here — Railway rejects the instruction.

EXPOSE 8000

CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
