#!/usr/bin/env python3
import os
import subprocess
import logging
import sqlite3
import json
import select
import hashlib
import tempfile
import threading
import urllib.parse
import numpy as np
import scipy.io.wavfile as wavfile
import tensorflow as tf
import tensorflow_hub as hub
import csv
import paho.mqtt.client as mqtt
import requests
from datetime import datetime, timezone
from pathlib import Path

# ── ENVIRONMENT ─────────────────────────────────────────────────────
RTSP_URL       = os.environ['RTSP_URL']
SR             = int(os.environ.get('SAMPLE_RATE', 16000))
CHUNK_DUR      = int(os.environ.get('CHUNK_DURATION', 5))
OUTPUT_DIR     = os.environ.get('OUTPUT_DIR', '/data')
DB_PATH        = os.environ.get('DB_PATH', '/data/birdnet.db')
LAT            = os.environ.get('LAT', '0.0')
LON            = os.environ.get('LON', '0.0')
SF_THRESH      = os.environ.get('SF_THRESH', '0.10')
YAMNET_THRESH  = float(os.environ.get('YAMNET_THRESH', '0.25'))
MIN_CONFIDENCE = float(os.environ.get('MIN_CONFIDENCE', '0.10'))

# MQTT Settings
MQTT_HOST      = os.environ.get('MQTT_HOST', 'localhost')
MQTT_PORT      = int(os.environ.get('MQTT_PORT', 1883))
MQTT_USER      = os.environ.get('MQTT_USER', '')
MQTT_PASS      = os.environ.get('MQTT_PASS', '')
MQTT_TOPIC     = os.environ.get('MQTT_TOPIC', 'birdnet/detection')

# BirdWeather integration (disabled by default)
# Token can be set via env var OR via web config file
def _get_birdweather_token():
    token = os.environ.get('BIRDWEATHER_TOKEN', '')
    if not token:
        config_path = os.environ.get('CONFIG_PATH', '/data/config.json')
        try:
            with open(config_path) as f:
                token = json.load(f).get('birdweather_token', '')
        except Exception:
            pass
    return token

BIRDWEATHER_TOKEN = _get_birdweather_token()

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
logger.info("Config: LAT=%s LON=%s YAMNET_THRESH=%s MIN_CONFIDENCE=%s", LAT, LON, YAMNET_THRESH, MIN_CONFIDENCE)

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
        mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="birdnet-detector")
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

# ── BIRDWEATHER ────────────────────────────────────────────────────
# Build common_name -> scientific_name lookup from BirdNET labels
_scientific_names = {}
_LABELS_JSON = '/usr/local/lib/python3.11/site-packages/birdnet_analyzer/checkpoints/V2.4/BirdNET_GLOBAL_6K_V2.4_Model_TFJS/static/model/labels.json'

def _load_scientific_names():
    global _scientific_names
    try:
        with open(_LABELS_JSON) as f:
            for entry in json.load(f):
                sci, common = entry.split('_', 1)
                _scientific_names[common] = sci
        logger.info("Loaded %d scientific name mappings", len(_scientific_names))
    except Exception as e:
        logger.warning("Could not load BirdNET labels for scientific names: %s", e)

def _wav_to_flac(wav_path):
    """Convert WAV to FLAC bytes using ffmpeg."""
    with tempfile.NamedTemporaryFile(suffix='.flac', delete=False) as tmp:
        flac_path = tmp.name
    try:
        subprocess.run([
            'ffmpeg', '-i', wav_path,
            '-c:a', 'flac', '-f', 'flac', '-y', flac_path
        ], check=True, capture_output=True)
        with open(flac_path, 'rb') as f:
            return f.read()
    finally:
        if os.path.exists(flac_path):
            os.unlink(flac_path)

def _submit_birdweather(wav_path, common_name, confidence, timestamp_iso):
    """Submit a detection to BirdWeather (called in background thread)."""
    try:
        scientific_name = _scientific_names.get(common_name, '')
        if not scientific_name:
            logger.warning("BirdWeather: no scientific name for '%s', skipping", common_name)
            return

        base_url = f"https://app.birdweather.com/api/v1/stations/{BIRDWEATHER_TOKEN}"

        # Parse the UTC timestamp and format with offset for BirdWeather
        dt = datetime.fromisoformat(timestamp_iso.replace('Z', '+00:00'))
        ts = dt.isoformat(timespec='milliseconds')
        ts_end = dt.replace(second=min(dt.second + CHUNK_DUR, 59)).isoformat(timespec='milliseconds')

        # Step 1: Upload audio as FLAC
        flac_data = _wav_to_flac(wav_path)
        soundscape_url = (
            f"{base_url}/soundscapes"
            f"?timestamp={urllib.parse.quote(ts)}&type=flac"
        )
        resp = requests.post(
            soundscape_url,
            data=flac_data,
            headers={'Content-Type': 'application/octet-stream', 'User-Agent': 'BirdNET-Analyzer'},
            timeout=45
        )
        if resp.status_code != 201:
            logger.warning("BirdWeather soundscape upload failed: %d %s", resp.status_code, resp.text[:200])
            return

        soundscape_id = resp.json()['soundscape']['id']

        # Step 2: Post detection
        payload = {
            "timestamp": ts,
            "lat": float(LAT),
            "lon": float(LON),
            "soundscapeId": soundscape_id,
            "soundscapeStartTime": ts,
            "soundscapeEndTime": ts_end,
            "commonName": common_name,
            "scientificName": scientific_name,
            "algorithm": "2p4",
            "confidence": f"{confidence:.2f}"
        }
        resp = requests.post(f"{base_url}/detections", json=payload, timeout=45)
        if resp.status_code != 201:
            logger.warning("BirdWeather detection post failed: %d %s", resp.status_code, resp.text[:200])
            return

        logger.info("BirdWeather: submitted %s (%.0f%%) soundscape=%d", common_name, confidence * 100, soundscape_id)

    except Exception as e:
        logger.warning("BirdWeather submission failed: %s", e)

def submit_to_birdweather(wav_path, common_name, confidence, timestamp_iso):
    """Fire-and-forget BirdWeather submission in a background thread."""
    if not BIRDWEATHER_TOKEN:
        return
    if wav_path is None or not os.path.exists(wav_path):
        return
    t = threading.Thread(target=_submit_birdweather, args=(wav_path, common_name, confidence, timestamp_iso), daemon=True)
    t.start()

# ── LOAD YAMNET ─────────────────────────────────────────────────────
logger.info("Loading YAMNet model from TF-Hub…")
yamnet_model = hub.load("https://tfhub.dev/google/yamnet/1")
with open('/app/yamnet_class_map.csv', 'r') as f:
    reader = csv.reader(f)
    next(reader)  # skip header
    yamnet_labels = {int(row[0]): row[2] for row in reader}

# YAMNet class indices for all bird-related sounds (explicit to avoid false positives
# from substring matching, e.g. "microwave" contains "crow", "howl" contains "owl")
BIRD_CLASS_INDICES = {
    93,   # Fowl
    94,   # Chicken, rooster
    96,   # Crowing, cock-a-doodle-doo
    99,   # Duck
    100,  # Quack
    101,  # Goose
    106,  # Bird
    107,  # Bird vocalization, bird call, bird song
    108,  # Chirp, tweet
    109,  # Squawk
    110,  # Pigeon, dove
    111,  # Coo
    112,  # Crow
    113,  # Caw
    114,  # Owl
    115,  # Hoot
    116,  # Bird flight, flapping wings
}
for idx in BIRD_CLASS_INDICES:
    logger.info("Bird class %d: %s", idx, yamnet_labels.get(idx, '?'))

# ── FFMPEG PROCESS ──────────────────────────────────────────────────
def get_ffmpeg_proc():
    cmd = [
        "ffmpeg", "-rtsp_transport", "tcp", "-i", RTSP_URL,
        "-vn",
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

    # Per-frame max: catches brief sounds (e.g. owl hoot) that get averaged away by mean
    max_scores = tf.reduce_max(scores, axis=0).numpy()
    top10 = np.argsort(max_scores)[-10:][::-1]

    # Diagnostic logging uses mean scores for stable trend monitoring
    if _diag_counter % 60 == 0:
        mean_scores = tf.reduce_mean(scores, axis=0).numpy()
        diag_top10 = np.argsort(mean_scores)[-10:][::-1]
        top_labels = [(yamnet_labels.get(i, '?'), mean_scores[i]) for i in diag_top10[:5]]
        logger.info("YAMNet top-5: %s", ", ".join(f"{l} ({s:.3f})" for l, s in top_labels))

    for idx in top10:
        if idx in BIRD_CLASS_INDICES and max_scores[idx] >= YAMNET_THRESH:
            label = yamnet_labels.get(idx, f"class_{idx}")
            logger.info("YAMNet detected bird: %s (max=%.3f)", label, max_scores[idx])
            return True
    return False

# ── SAVE AUDIO ──────────────────────────────────────────────────────
def save_wav(path, audio_np):
    from scipy.signal import butter, sosfilt
    # Apply 200Hz highpass to clean up wind/traffic in saved recordings
    sos = butter(4, 200, btype='highpass', fs=SR, output='sos')
    filtered = sosfilt(sos, audio_np.astype(np.float32)).astype(np.int16)
    wavfile.write(path, SR, filtered)
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
    week = os.environ.get('WEEK', '') or str(datetime.utcnow().isocalendar()[1])
    logger.info("Analyzing with BirdNET-Analyzer: %s (week=%s)", wav_path, week)
    try:
        subprocess.run([
            "python3", "-m", "birdnet_analyzer.analyze",
            "-o", OUTPUT_DIR,
            "--lat", LAT,
            "--lon", LON,
            "--week", week,
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
    _load_scientific_names()
    if BIRDWEATHER_TOKEN:
        logger.info("BirdWeather integration enabled (token set)")
    cleanup_old_txt_files()

    SPECIES_COOLDOWN = 3600  # seconds before saving audio for same species again
    recent_species = {}      # species_code -> last detection datetime

    proc = get_ffmpeg_proc()
    chunk_count = 0
    last_chunk_hash = None
    stale_count = 0
    STALE_LIMIT = 3  # consecutive identical chunks before reconnect
    try:
        while True:
            # Wait for data with timeout to detect hung streams
            ready, _, _ = select.select([proc.stdout], [], [], READ_TIMEOUT)
            if not ready:
                logger.warning("Read timeout (%ds), no data from stream - reconnecting…", READ_TIMEOUT)
                proc.kill()
                proc = get_ffmpeg_proc()
                chunk_count = 0
                last_chunk_hash = None
                stale_count = 0
                continue

            raw = proc.stdout.read(CHUNK_SIZE)
            if len(raw) != CHUNK_SIZE:
                logger.warning("Short read (%d bytes), reconnecting…", len(raw))
                proc.kill()
                proc = get_ffmpeg_proc()
                chunk_count = 0
                last_chunk_hash = None
                stale_count = 0
                continue

            # Detect stale/frozen audio stream
            chunk_hash = hashlib.md5(raw).hexdigest()
            if chunk_hash == last_chunk_hash:
                stale_count += 1
                if stale_count >= STALE_LIMIT:
                    logger.error("Stale audio detected: %d consecutive identical chunks (%ds) - reconnecting…",
                                 stale_count, stale_count * CHUNK_DUR)
                    proc.kill()
                    proc = get_ffmpeg_proc()
                    chunk_count = 0
                    last_chunk_hash = None
                    stale_count = 0
                    continue
            else:
                stale_count = 0
            last_chunk_hash = chunk_hash

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
                    best = max(detections, key=lambda x: x['confidence'])
                    now = datetime.utcnow()
                    species = best['species_code']
                    last_seen = recent_species.get(species)

                    if last_seen and (now - last_seen).total_seconds() < SPECIES_COOLDOWN:
                        # Repeat species within cooldown — log, publish, but discard audio
                        os.remove(wav_path)
                        logger.info("Repeat species %s (%.1f%%), removed audio",
                                    best['common_name'], best['confidence'] * 100)
                        save_detection(timestamp_iso, best['common_name'], species,
                                       best['confidence'], None)
                        publish_detection(timestamp_iso, best['common_name'], species,
                                          best['confidence'], None)
                    else:
                        # New or cooled-down species — keep the recording
                        logger.info("New species detection: %s (%.1f%%)",
                                    best['common_name'], best['confidence'] * 100)
                        save_detection(timestamp_iso, best['common_name'], species,
                                       best['confidence'], wav_path)
                        publish_detection(timestamp_iso, best['common_name'], species,
                                          best['confidence'], wav_path)
                        submit_to_birdweather(wav_path, best['common_name'],
                                              best['confidence'], timestamp_iso)
                    recent_species[species] = now
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
