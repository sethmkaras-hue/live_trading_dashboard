FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca; fi; npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data FRONTEND_DIST=/app/frontend/dist
WORKDIR /app
COPY requirements.txt ./
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 --create-home dashboard \
    && install -d -o dashboard -g dashboard /data
COPY --chown=dashboard:dashboard app/ ./app/
COPY --from=frontend /build/dist ./frontend/dist
USER dashboard
EXPOSE 8080
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8080","--proxy-headers"]
