FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend:/app \
    TZ=Asia/Shanghai \
    XDG_CACHE_HOME=/tmp/.cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 campus \
    && useradd --uid 10001 --gid campus --create-home campus

WORKDIR /app/backend
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=campus:campus backend/app /app/backend/app
COPY --chown=campus:campus backend/mcp_server.py /app/backend/mcp_server.py
COPY --chown=campus:campus deploy /app/deploy
RUN mkdir -p /app/backend/uploads /app/backend/private_documents /app/backend/chroma_data /app/demo_state \
    && chown -R campus:campus /app/backend/uploads /app/backend/private_documents /app/backend/chroma_data /app/demo_state

USER campus
CMD ["python", "-m", "uvicorn", "deploy.demo.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
