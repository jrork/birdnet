FROM python:3.11-slim

# 1) Install system tools
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      git \
      curl \
      build-essential \
      libsndfile1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2) Fetch YAMNet class map
RUN curl -L \
    https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv \
    -o /app/yamnet_class_map.csv

# 3) Install Python dependencies + BirdNET-Analyzer
RUN pip install --upgrade pip setuptools wheel \
 && pip install --no-cache-dir \
      tensorflow \
      tensorflow-hub \
      numpy \
      scipy \
      soundfile \
      paho-mqtt \
      flask \
      gunicorn \
      docker \
      git+https://github.com/kahst/BirdNET-Analyzer.git@main

# 3b) Pin setuptools <82 — tensorflow-hub needs pkg_resources, removed in 82+
RUN pip install "setuptools<82"

# 4) Copy scripts
COPY stream_birdnet.py /app/stream_birdnet.py
COPY web_app.py /app/web_app.py

# 5) Mount point for output clips and database
VOLUME /data

# 6) Default env vars (override with -e)
ENV RTSP_URL="rtsp://192.168.1.10:554/stream" \
    SAMPLE_RATE=16000 \
    CHUNK_DURATION=5 \
    LAT=0.0 \
    LON=0.0 \
    WEEK="" \
    SF_THRESH=0.10 \
    YAMNET_THRESH=0.25 \
    MIN_CONFIDENCE=0.25 \
    OUTPUT_DIR=/data \
    DB_PATH=/data/birdnet.db \
    MQTT_HOST=localhost \
    MQTT_PORT=1883 \
    MQTT_TOPIC=birdnet/detection

# 7) Default command (override in compose)
CMD ["python3", "stream_birdnet.py"]
