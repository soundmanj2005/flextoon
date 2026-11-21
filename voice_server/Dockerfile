# Build from the repository root:
#   docker build -f voice_server/Dockerfile -t flextoon-voice .
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY voice_server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY voice_server /app/voice_server

# Reference recordings and downloaded model files live on mounted volumes.
ENV VOICES_DIR=/voices HF_HOME=/models VOICE_BACKEND=chatterbox
VOLUME ["/voices", "/models"]
EXPOSE 8000

# One worker so the model loads once; threads serve requests, a lock serialises synthesis.
CMD ["gunicorn", "-w", "1", "--threads", "4", "-b", "0.0.0.0:8000", "--timeout", "900", "voice_server.server:app"]
