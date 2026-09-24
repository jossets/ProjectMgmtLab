FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY templates ./templates
COPY static ./static
COPY cours ./cours

# non-root: /app/data (SQLite db + uploads) is chowned so a fresh named
# volume mounted there inherits the right owner on first use. For a host
# bind mount instead, make sure the host directory is writable by uid 1000
# (or override --user at `docker run` time).
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/')" || exit 1

# single worker/container only: rooms/connections for the whiteboard and
# Kanban real-time WebSocket sync, and the login rate-limiter, are kept in
# in-memory process state. Running multiple workers (or multiple replicas
# of this container without session affinity) would split that state, so
# two students on the same board could land on different workers and never
# see each other's updates. Don't add --workers or scale replicas without
# first moving that shared state to something external (e.g. Redis).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
