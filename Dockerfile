# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/chkn-11/chapterise" \
      org.opencontainers.image.description="Local audiobook chapter review with EPUB alignment and speech recognition"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models \
    HF_HUB_DISABLE_TELEMETRY=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-transcription.txt ./
RUN pip install --no-cache-dir -r requirements-transcription.txt

RUN groupadd --gid 10001 chapterise \
    && useradd --uid 10001 --gid chapterise --create-home chapterise \
    && mkdir /data /models \
    && chown chapterise:chapterise /data /models

COPY app.py epub_match.py persistence.py transcription.py ./
COPY static/ ./static/

USER chapterise
EXPOSE 8765
STOPSIGNAL SIGINT
ENTRYPOINT ["python", "app.py"]
CMD ["--host", "0.0.0.0", "--port", "8765", "--workspace", "/data"]
