#!/usr/bin/env python3
import os
import subprocess
import logging
import sqlite3
import json
import select
import numpy as np
import scipy.io.wavfile as wavfile
import tensorflow as tf
import tensorflow_hub as hub
import csv
import paho.mqtt.client as mqtt
from datetime import datetime
from pathlib import Path

# ── ENVIRONMENT ─────────────────────────────────────────────────────
RTSP_URL       = os.environ['RTSP_URL']
SR             = int(os.environ.get('SAMPLE_RATE', 16000))
CHUNK_DUR      = int(os.environ.get('CHUNK_DURATION', 5))
OUTPUT_DIR     = os.environ.get('OUTPUT_DIR', '/data')
DB_PATH        = os.environ.get('DB_PATH', '/data/birdnet.db')
LAT            = os.environ.get('LAT', '0.0')
LON            = os.environ.get('LON', '0.0')
WEEK           = os.environ.get('WEEK', str(datetime.utcnow().isocalendar()[1]))
SF_THRESH      = os.environ.get('SF_THRESH', '0.10')
YAMNET_THRESH  = float(os.environ.get('YAMNET_THRESH', '0.25'))
MIN_CONFIDENCE = float(os.environ.get('MIN_CONFIDENCE', '0.10'))

# MQTT Settings
MQTT_HOST      = os.environ.get('MQTT_HOST', 'localhost')
MQTT_PORT      = int(os.environ.get('MQTT_PORT', 1883))
MQTT_USER      = os.environ.get('MQTT_USER', '')
MQTT_PASS      = os.environ.get('MQTT_PASS', '')
MQTT_TOPIC     = os.environ.get('MQTT_TOPIC', 'birdnet/detection')

CHANNELS    = 1
BYTES_PER_S = 2
CHUNK_SIZE  = SR * CHUNK_DUR * BYTES_PER_S
READ_TIMEOUT = int(os.environ.get('READ_TIMEOUT', 30))  # seconds before restarting FFmpeg
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── LOGGING ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("BirdStream")

# ── DATABASE ────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            common_name TEXT NOT NULL,
            species_code TEXT NOT NULL,
            confidence REAL NOT NULL,
            audio_file TEXT,
            latitude REAL,
            longitude REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON detections(timestamp)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_species ON detections(species_code)')
    conn.commit()
    conn.close()
    logger.info("Database initialized at %s", DB_PATH)

def save_detection(timestamp, common_name, species_code, confidence, audio_file):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        INSERT INTO detections (timestamp, common_name, species_code, confidence, audio_file, latitude, longitude)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (timestamp, common_name, species_code, confidence, audio_file, float(LAT), float(LON)))
    conn.commit()
    conn.close()
    logger.info("Saved detection to DB: %s (%.1f%%)", common_name, confidence * 100)

# ── MQTT ────────────────────────────────────────────────────────────
mqtt_client = None

def init_mqtt():
    global mqtt_client
    try:
        mqtt_client = mqtt.Client(client_id="birdnet-detector")
        if MQTT_USER:
            mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
        mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
        mqtt_client.loop_start()
        logger.info("Connected to MQTT broker at %s:%d", MQTT_HOST, MQTT_PORT)
    except Exception as e:
        logger.warning("Failed to connect to MQTT: %s", e)
        mqtt_client = None

def publish_detection(timestamp, common_name, species_code, confidence, audio_file):
    if mqtt_client is None:
        return
    payload = json.dumps({
        "timestamp": timestamp,
        "common_name": common_name,
        "species_code": species_code,
        "confidence": round(confidence, 4),
        "audio_url": f"/audio/{os.path.basename(audio_file)}" if audio_file else None,
        "latitude": float(LAT),
        "longitude": float(LON)
    })
    mqtt_client.publish(MQTT_TOPIC, payload, qos=1)
    logger.info("Published to MQTT: %s", MQTT_TOPIC)

# ── LOAD YAMNET ─────────────────────────────────────────────────────
logger.info("Loading YAMNet model from TF-Hub…")
yamnet_model = hub.load("https://tfhub.dev/google/yamnet/1")
with open('/app/yamnet_class_map.csv', 'r') as f:
    reader = csv.reader(f)
    next(reader)  # skip header
    yamnet_labels = {int(row[0]): row[2] for row in reader}

# YAMNet class indices for all bird-related sounds
BIRD_CLASS_INDICES = set()
BIRD_KEYWORDS = {'bird', 'chirp', 'tweet', 'squawk', 'pigeon', 'dove', 'crow',
                 'caw', 'owl', 'hoot', 'fowl', 'chicken', 'rooster', 'crowing',
                 'cock-a-doodle', 'duck', 'quack', 'goose', 'honk',
                 'bird flight', 'flapping wings', 'coo'}
for idx, name in yamnet_labels.items():
    if any(kw in name.lower() for kw in BIRD_KEYWORDS):
        BIRD_CLASS_INDICES.add(idx)
        logger.info("Bird class %d: %s", idx, name)

# ── FFMPEG PROCESS ──────────────────────────────────────────────────
def get_ffmpeg_proc():
    filter_chain = "highpass=f=200,lowpass=f=8000,afftdn"
    cmd = [
        "ffmpeg", "-rtsp_transport", "tcp", "-i", RTSP_URL,
        "-vn", "-af", filter_chain,
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ac", str(CHANNELS), "-ar", str(SR), "-"
    ]
    logger.info("Launching FFmpeg: %s", " ".join(cmd))
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

# ── BIRD DETECTION (YAMNet pre-filter) ──────────────────────────────
_diag_counter = 0

def is_bird_present(audio_np):
    global _diag_counter
    _diag_counter += 1
    audio = audio_np.astype(np.float32) / 32768.0
    scores, _, _ = yamnet_model(audio)
    mean_scores = tf.reduce_mean(scores, axis=0).numpy()
    top10 = np.argsort(mean_scores)[-10:][::-1]

    # Log diagnostics every 60 chunks (~5 min at 5s chunks)
    if _diag_counter % 60 == 0:
        top_labels = [(yamnet_labels.get(i, '?'), mean_scores[i]) for i in top10[:5]]
        logger.info("YAMNet top-5: %s", ", ".join(f"{l} ({s:.3f})" for l, s in top_labels))

    for idx in top10:
        if idx in BIRD_CLASS_INDICES and mean_scores[idx] >= YAMNET_THRESH:
            label = yamnet_labels.get(idx, f"class_{idx}")
            logger.info("YAMNet detected bird: %s (%.3f)", label, mean_scores[idx])
            return True
    return False

# ── SAVE AUDIO ──────────────────────────────────────────────────────
def save_wav(path, audio_np):
    wavfile.write(path, SR, audio_np)
    logger.info("Saved WAV: %s", path)

# ── PARSE BIRDNET RESULTS ───────────────────────────────────────────
def parse_birdnet_results(txt_path):
    """Parse BirdNET selection table and return list of detections."""
    detections = []
    if not os.path.exists(txt_path):
        return detections
    
    with open(txt_path, 'r') as f:
        lines = f.readlines()
    
    # Skip header line
    for line in lines[1:]:
        parts = line.strip().split('\t')
        if len(parts) >= 10:
            common_name = parts[7]
            species_code = parts[8]
            confidence = float(parts[9])
            if confidence >= MIN_CONFIDENCE:
                detections.append({
                    'common_name': common_name,
                    'species_code': species_code,
                    'confidence': confidence
                })
    
    # Remove the text file after parsing
    os.remove(txt_path)
    return detections

# ── SEND TO BIRDNET ─────────────────────────────────────────────────
def analyze_with_birdnet(wav_path):
    logger.info("Analyzing with BirdNET-Analyzer: %s", wav_path)
    try:
        subprocess.run([
            "python3", "-m", "birdnet_analyzer.analyze",
            "-o", OUTPUT_DIR,
            "--lat", LAT,
            "--lon", LON,
            "--week", WEEK,
            "--sf_thresh", SF_THRESH,
            "--min_conf", str(MIN_CONFIDENCE),
            "--top_n", "3",
            "--overlap", "0.5",
            wav_path
        ], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        logger.error("BirdNET analysis failed: %s", e)
        return []
    
    # Parse results from the generated text file
    base_name = os.path.splitext(os.path.basename(wav_path))[0]
    txt_path = os.path.join(OUTPUT_DIR, f"{base_name}.BirdNET.selection.table.txt")
    return parse_birdnet_results(txt_path)

# ── CLEANUP OLD FILES ───────────────────────────────────────────────
def cleanup_old_txt_files():
    """Remove any leftover .txt and .csv files from previous runs."""
    for pattern in ['*.txt', '*.csv']:
        for f in Path(OUTPUT_DIR).glob(pattern):
            try:
                f.unlink()
                logger.info("Cleaned up: %s", f)
            except Exception as e:
                logger.warning("Failed to delete %s: %s", f, e)

# ── MAIN LOOP ──────────────────────────────────────────────────────
def main():
    init_db()
    init_mqtt()
    cleanup_old_txt_files()

    proc = get_ffmpeg_proc()
    chunk_count = 0
    try:
        while True:
            # Wait for data with timeout to detect hung streams
            ready, _, _ = select.select([proc.stdout], [], [], READ_TIMEOUT)
            if not ready:
                logger.warning("Read timeout (%ds), no data from stream - reconnecting…", READ_TIMEOUT)
                proc.kill()
                proc = get_ffmpeg_proc()
                chunk_count = 0
                continue

            raw = proc.stdout.read(CHUNK_SIZE)
            if len(raw) != CHUNK_SIZE:
                logger.warning("Short read (%d bytes), reconnecting…", len(raw))
                proc.kill()
                proc = get_ffmpeg_proc()
                chunk_count = 0
                continue

            chunk_count += 1
            # Log heartbeat every 12 chunks (~1 minute at 5s chunks)
            if chunk_count % 12 == 0:
                logger.info("Heartbeat: processed %d chunks", chunk_count)

            audio_np = np.frombuffer(raw, dtype=np.int16)
            if is_bird_present(audio_np):
                ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                timestamp_iso = datetime.utcnow().isoformat() + "Z"
                wav_path = os.path.join(OUTPUT_DIR, f"bird_{ts}.wav")
                save_wav(wav_path, audio_np)
                
                detections = analyze_with_birdnet(wav_path)
                
                if detections:
                    # Use highest confidence detection
                    best = max(detections, key=lambda x: x['confidence'])
                    save_detection(
                        timestamp_iso,
                        best['common_name'],
                        best['species_code'],
                        best['confidence'],
                        wav_path
                    )
                    publish_detection(
                        timestamp_iso,
                        best['common_name'],
                        best['species_code'],
                        best['confidence'],
                        wav_path
                    )
                else:
                    # No confident detection, remove the wav file
                    os.remove(wav_path)
                    logger.info("No confident detection, removed %s", wav_path)
                    
    except Exception:
        logger.exception("Stream halted unexpectedly")
    finally:
        proc.kill()
        if mqtt_client:
            mqtt_client.loop_stop()

if __name__ == "__main__":
    main()
