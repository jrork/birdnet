
```
    ____  _         ___   _____________
   / __ )(_)______ / / | / / ____/_  __/
  / __  / / ___/ // /  |/ / __/   / /
 / /_/ / / /  / __  / /|  / /___  / /
/_____/_/_/  /_/ /_/_/ |_/_____/ /_/
   ___              __
  / _ \___ ___ ____/ /____  ____
 / , _/ -_) _ `/ _  / __/ |/ / /
/_/|_|\__/\_,_/\_,_/\__/|___/_/
```

# BirdNET Realtime

**Turn any security camera into an AI-powered bird observatory.**

BirdNET Realtime taps into an RTSP audio stream, runs a two-stage neural network pipeline, and tells you exactly which birds are singing in your yard - in real time.

---

## How It Works

```
                         ┌──────────────┐
   RTSP Camera           │   FFmpeg     │    Bandpass 200-8000 Hz
   Audio Stream  ──────► │   Decode &   │──► + Noise Reduction
                         │   Filter     │
                         └──────┬───────┘
                                │
                          5-sec chunks
                                │
                                ▼
                    ┌───────────────────────┐
                    │   Stage 1: YAMNet     │    "Is there a bird
                    │   (Google, TF-Hub)    │──►  in this audio?"
                    └───────────┬───────────┘
                                │
                          bird detected?
                         no /        \ yes
                           │          │
                        discard    save .wav
                                      │
                                      ▼
                    ┌───────────────────────┐
                    │  Stage 2: BirdNET     │    "What species
                    │  (Cornell Lab)        │──►  is this?"
                    └───────────┬───────────┘
                                │
                                ▼
              ┌─────────┬───────┴────────┐
              │         │                │
          SQLite DB   MQTT Pub     Web Dashboard
          (history)   (automation)   (monitoring)
```

**Stage 1 (YAMNet)** is a fast general-purpose audio classifier that acts as a gate - it listens to every 5-second chunk and only passes audio forward when it hears bird-like sounds. This saves BirdNET from wasting compute on silence, wind, and traffic.

**Stage 2 (BirdNET)** is the [Cornell Lab's bird identification model](https://github.com/kahst/BirdNET-Analyzer). It takes the filtered audio and identifies the exact species, using your geographic coordinates and the current week to narrow down which birds are likely in your area.

---

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USER/BirdNET-Realtime.git
cd BirdNET-Realtime
cp .env.example .env
```

Edit `.env` with your RTSP camera URL and MQTT credentials:

```env
RTSP_URL=rtsp://user:password@192.168.1.10:554/stream
MQTT_HOST=your-mqtt-broker.com
MQTT_USER=birdnet
MQTT_PASS=your-mqtt-password
```

### 2. Launch

```bash
docker-compose up -d --build
```

### 3. Open the dashboard

Browse to **http://localhost:5088** - you'll see detections roll in as birds visit.

---

## Web Dashboard

The built-in web interface gives you:

- **Live detection feed** with species name, confidence score, and audio playback
- **Detection statistics** - today, this week, unique species, all-time totals
- **Settings panel** - tune thresholds without restarting
- **Log viewer** - watch the detector in real time
- **Detector status** - at-a-glance health check (listening / stale / offline)

---

## Home Automation via MQTT

Every detection publishes a JSON payload, ready for Home Assistant, Node-RED, or any MQTT consumer:

```json
{
  "timestamp": "2026-02-11T14:30:00Z",
  "common_name": "Northern Cardinal",
  "species_code": "norcar",
  "confidence": 0.85,
  "audio_url": "/audio/bird_20260211_143000.wav",
  "latitude": 40.758,
  "longitude": -73.986
}
```

**Ideas:** flash a light when a rare species visits, keep a daily bird count on a dashboard, trigger a camera snapshot, log to a spreadsheet.

---

## Configuration

All settings are controlled via environment variables. Non-secret values go in `docker-compose.yaml`, secrets go in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `RTSP_URL` | *(required)* | RTSP stream URL (set in `.env`) |
| `YAMNET_THRESH` | `0.25` | YAMNet pre-filter sensitivity (lower = more sensitive) |
| `MIN_CONFIDENCE` | `0.25` | Minimum BirdNET confidence to save a detection |
| `SF_THRESH` | `0.10` | Species frequency threshold |
| `LAT`, `LON` | `0.0, 0.0` | Your coordinates (for regional species filtering) |
| `WEEK` | *(auto)* | Auto-calculated from current date. Override with 1-52 |
| `MQTT_HOST` | *(required)* | MQTT broker hostname (set in `.env`) |
| `MQTT_USER` | | MQTT username (set in `.env`) |
| `MQTT_PASS` | | MQTT password (set in `.env`) |

---

## API

The web server exposes a REST API for programmatic access:

```
GET  /api/detections?limit=50   Recent detections
GET  /api/stats                 Detection counts & species stats
GET  /api/config                Current configuration
POST /api/config                Update configuration
GET  /api/detector-status       Detector health (listening/stale/offline)
GET  /api/logs?lines=100        Detector container logs
POST /api/restart               Restart the detector
GET  /audio/<filename>          Stream a detection audio clip
GET  /health                    Health check
```

---

## Architecture

Two Docker containers, one shared volume:

| Container | Role | Entry Point |
|-----------|------|-------------|
| `birdnet-detector` | Reads RTSP stream, runs inference, writes DB + MQTT | `stream_birdnet.py` |
| `birdnet-web` | Flask dashboard served via Gunicorn | `web_app.py` |

Both containers share the `/data` volume containing the SQLite database and saved audio clips.

---

## Troubleshooting

**Birds are singing but nothing is detected?**

1. Check the logs for `YAMNet top-5:` entries (logged every 5 min) to see what the model is hearing
2. If bird classes appear but below threshold, lower `YAMNET_THRESH` (try `0.10`)
3. If only "Silence" or "Wind" appear, the mic may be too far away or too noisy
4. Make sure `WEEK` isn't hardcoded to the wrong month - it affects which species BirdNET looks for

**Detector shows "stale" or "offline"?**

The RTSP stream may have dropped. Check logs for reconnection messages. The detector auto-reconnects after a 30-second timeout (configurable via `READ_TIMEOUT`).

---

## Tech Stack

| Component | What | Why |
|-----------|------|-----|
| [BirdNET-Analyzer](https://github.com/kahst/BirdNET-Analyzer) | Bird species identification | Cornell Lab's state-of-the-art model, 6000+ species |
| [YAMNet](https://tfhub.dev/google/yamnet/1) | General audio classification | Fast pre-filter so BirdNET only runs on bird audio |
| [TensorFlow](https://www.tensorflow.org/) | ML runtime | Powers both YAMNet and BirdNET inference |
| [FFmpeg](https://ffmpeg.org/) | Audio decoding & filtering | RTSP decode, bandpass filter, noise reduction |
| [Flask](https://flask.palletsprojects.com/) | Web framework | Lightweight dashboard and API |
| [SQLite](https://www.sqlite.org/) | Database | Zero-config persistent storage |
| [MQTT](https://mqtt.org/) | Message broker protocol | Real-time home automation integration |
| [Docker](https://www.docker.com/) | Containerization | Single-command deployment |

---

## License

MIT
