# Signaling server (+ presence + TCP reflector). It never carries file data. The desktop app runs on users' PCs.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY server/ server/

RUN useradd --system --uid 10001 p2p
USER p2p

# 8765 signaling + presence (HTTP/WebSocket), 8766 TCP reflector
EXPOSE 8765 8766

HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2)" || exit 1

CMD ["python", "-m", "server.main"]
