# Free speech server (D52): faster-whisper Small INT8 (speech-to-text) + Piper (text-to-speech) on CPU,
# behind an OpenAI-compatible API that livekit-plugins-openai talks to. Own image (Python 3.12), linux/amd64.
#   docker build -t kairox/speech:dev services/speech
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    MODELS=/models HF_HOME=/models/hf PORT=8100 \
    STT_MODEL=small STT_COMPUTE=int8 STT_THREADS=4 PIPER_VOICES=en,ar,hi,zh

COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt \
 && useradd --uid 10002 --create-home speech \
 && mkdir -p /models && chown speech:speech /models

COPY app.py download.py /srv/
USER speech
WORKDIR /srv
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz',timeout=4)"
# models are downloaded on first start into the /models volume, then reused
CMD ["sh", "-c", "python download.py && exec python app.py"]
